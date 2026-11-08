"""Synchronous and asynchronous MusicBrainz clients."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping
from typing import Any, Generic, Literal, Self, TypeVar

import anyio
import httpx2

from . import _constants as C
from . import models as M
from ._core import Call, Core, Filter, Includes, decode_body, raise_for_response, retry_delay
from ._ratelimit import AsyncRateLimiter, RateLimiter
from ._version import __version__
from .errors import AuthenticationRequiredError, NetworkError, NotFoundError, ValidationError

T = TypeVar("T")
R = TypeVar("R")
Vote = Literal["upvote", "downvote", "withdraw"]


# =========================================================================== shared config


class _Config:
    def __init__(
        self,
        app: str,
        version: str,
        contact: str | None,
        *,
        username: str | None,
        password: str | None,
        oauth_token: str | None,
        auth: httpx2.Auth | None,
        base_url: str,
        max_retries: int,
        backoff_factor: float,
        validate: bool,
    ):
        if not app or not version:
            raise ValidationError("app and version are required to build a meaningful User-Agent")
        self.user_agent = f"{app}/{version}" + (f" ( {contact} )" if contact else "") + f" python-musicbrainz/{__version__}"
        if sum(x is not None for x in (password, oauth_token, auth)) > 1:
            raise ValidationError("pass only one of username/password, oauth_token or auth")
        if password is not None:
            if not username:
                raise ValidationError("username is required with password")
            auth = httpx2.DigestAuth(username, password)
        self.auth = auth
        self.headers = {"User-Agent": self.user_agent}
        if oauth_token is not None:
            self.headers["Authorization"] = f"Bearer {oauth_token}"
        self.has_auth = auth is not None or oauth_token is not None
        # The API wants "<app>-<version>" where version contains no hyphen.
        client_id = f"{app}-{version}" if "-" not in version else None
        self.core = Core(client_id=client_id, has_auth=self.has_auth, validate=validate)
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def check_auth(self, call: Call[Any]) -> None:
        if call.requires_auth and not self.has_auth:
            raise AuthenticationRequiredError(f"{call.method} {call.path} requires authentication")


def _retryable_exc(method: str, exc: Exception) -> bool:
    # Never risk replaying a submission whose request may already have reached the server.
    if method == "GET":
        return isinstance(exc, httpx2.TransportError)
    return isinstance(exc, (httpx2.ConnectError, httpx2.ConnectTimeout))


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# =========================================================================== sync


class EntityResource(Generic[T]):
    """Lookup / browse / search for one entity type, e.g. ``mb.artists``."""

    def __init__(self, client: MusicBrainz, entity: str):
        self._client = client
        self._core = client._config.core
        self.entity = entity

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.entity}>"

    def get(self, mbid: str, *, inc: Includes = None, release_type: Filter = None,
            release_status: Filter = None) -> T:
        """Look up one entity by MBID.

        ``inc`` takes a list (or ``+``/space separated string) of includes, e.g.
        ``["aliases", "url-rels"]``. ``release_type``/``release_status`` filter included
        releases / release groups (``"album"``, ``["album", "ep"]``, ``"official"``...).
        """
        return self._client._run(self._core.lookup(self.entity, mbid, inc, release_type, release_status))

    def browse(self, *, inc: Includes = None, limit: int | None = None, offset: int | None = None,
               release_type: Filter = None, release_status: Filter = None,
               release_group_status: str | None = None, **linked: str) -> M.Page[T]:
        """Browse entities linked to another entity, e.g. ``mb.releases.browse(artist=mbid)``.

        Use ``release_group=...`` for ``release-group`` and ``track_artist=...`` as-is.
        """
        return self._client._run(self._core.browse(
            self.entity, linked, inc, limit, offset, release_type, release_status, release_group_status))

    def iter_browse(self, *, inc: Includes = None, page_size: int = C.MAX_LIMIT, max_items: int | None = None,
                    release_type: Filter = None, release_status: Filter = None,
                    release_group_status: str | None = None, **linked: str) -> Iterator[T]:
        """Iterate over all browse results, fetching pages as needed."""
        return self._client._paginate(
            lambda off: self._core.browse(self.entity, linked, inc, page_size, off, release_type, release_status,
                                          release_group_status),
            max_items,
        )

    def search(self, query: str | None = None, *, limit: int | None = None, offset: int | None = None,
               dismax: bool = False, **fields: object) -> M.Page[T]:
        """Search, e.g. ``mb.artists.search("nirvana", country="US")``.

        ``query`` is raw Lucene syntax; ``fields`` are added as ``field:"value"`` terms joined by AND.
        Each result has a ``score`` (0-100).
        """
        return self._client._run(self._core.search(self.entity, query, fields, limit, offset, dismax))

    def iter_search(self, query: str | None = None, *, page_size: int = C.MAX_LIMIT, max_items: int | None = None,
                    dismax: bool = False, **fields: object) -> Iterator[T]:
        """Iterate over search results, fetching pages as needed. Use ``max_items`` to stop early."""
        return self._client._paginate(
            lambda off: self._core.search(self.entity, query, fields, page_size, off, dismax), max_items)


class SearchOnlyResource(Generic[T]):
    """Entities that can only be searched (annotation, cdstub, tag)."""

    def __init__(self, client: MusicBrainz, entity: str):
        self._client = client
        self._core = client._config.core
        self.entity = entity

    search = EntityResource.search
    iter_search = EntityResource.iter_search


class UrlResource(EntityResource[M.Url]):
    def by_resource(self, url: str, *, inc: Includes = None) -> M.Url:
        """Look up a URL entity by its text (raises :class:`NotFoundError` if unknown)."""
        return self._client._run(self._core.lookup_urls([url], inc))[0]

    def by_resources(self, urls: Iterable[str], *, inc: Includes = None) -> list[M.Url]:
        """Look up many URLs (batched by 100). Unknown URLs are silently skipped."""
        result: list[M.Url] = []
        for chunk in _chunks(list(urls), C.URL_BATCH_SIZE):
            try:
                result += self._client._run(self._core.lookup_urls(chunk, inc))
            except NotFoundError:
                if len(chunk) > 1:
                    raise
        return result


class GenreResource(EntityResource[M.Genre]):
    def all(self, *, limit: int | None = None, offset: int | None = None) -> M.Page[M.Genre]:
        """One page of the alphabetical list of all genres."""
        return self._client._run(self._core.genres_all(limit, offset))

    def iter_all(self, *, page_size: int = C.MAX_LIMIT, max_items: int | None = None) -> Iterator[M.Genre]:
        return self._client._paginate(lambda off: self._core.genres_all(page_size, off), max_items)

    def names(self) -> list[str]:
        """All genre names (single plain-text request)."""
        return self._client._run(self._core.genre_names())


class CollectionResource(EntityResource[M.Collection]):
    def of_editor(self, editor: str, *, include_private: bool = False, limit: int | None = None,
                  offset: int | None = None) -> M.Page[M.Collection]:
        """Collections of an editor. ``include_private`` requires authenticating as that editor."""
        return self.browse(editor=editor, inc=["user-collections"] if include_private else None,
                           limit=limit, offset=offset)

    def summary(self, mbid: str, entity_type: str) -> M.Collection:
        """Collection description with a summary of its contents of ``entity_type``."""
        return self._client._run(self._core.lookup_collection_contents(mbid, entity_type))

    def items(self, mbid: str, entity_type: str, **kwargs: Any) -> M.Page[Any]:
        """One page of collection contents (full entities), e.g. ``items(mbid, "release")``."""
        return self._client._resource(entity_type).browse(collection=mbid, **kwargs)

    def iter_items(self, mbid: str, entity_type: str, **kwargs: Any) -> Iterator[Any]:
        return self._client._resource(entity_type).iter_browse(collection=mbid, **kwargs)

    def add(self, mbid: str, entity_type: str, mbids: Iterable[str]) -> None:
        """Add entities to a collection you own (batched automatically)."""
        for chunk in _chunks(list(mbids), C.COLLECTION_BATCH_SIZE):
            self._client._run(self._core.collection_modify("PUT", mbid, entity_type, chunk))

    def remove(self, mbid: str, entity_type: str, mbids: Iterable[str]) -> None:
        """Remove entities from a collection you own (batched automatically)."""
        for chunk in _chunks(list(mbids), C.COLLECTION_BATCH_SIZE):
            self._client._run(self._core.collection_modify("DELETE", mbid, entity_type, chunk))


class MusicBrainz:
    """Synchronous MusicBrainz WS/2 client.

    >>> with MusicBrainz("MyApp", "1.0", "me@example.com") as mb:
    ...     artist = mb.artists.get("5b11f4ce-a62d-471e-81fc-a69a8278c7da", inc=["aliases"])

    :param app: your application's name (used in the User-Agent and as submission client ID)
    :param version: your application's version (no hyphen if you want to submit data)
    :param contact: URL or e-mail where MusicBrainz admins can reach you
    :param username/password: digest auth; use a token from your MusicBrainz *Applications* page
    :param oauth_token: OAuth2 bearer token (alternative to digest)
    :param rate_limit: max requests per second (``None`` disables, e.g. for a private mirror)
    :param rate_limiter: a shared :class:`RateLimiter` (overrides ``rate_limit``)
    :param max_retries: retries on 429/502/503/504 and network errors
    :param validate: client-side checking of includes and filter values
    """

    def __init__(
        self,
        app: str,
        version: str,
        contact: str | None = None,
        *,
        username: str | None = None,
        password: str | None = None,
        oauth_token: str | None = None,
        auth: httpx2.Auth | None = None,
        base_url: str = C.DEFAULT_BASE_URL,
        rate_limit: float | None = 1.0,
        rate_limiter: RateLimiter | None = None,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        timeout: float = 30.0,
        validate: bool = True,
        transport: httpx2.BaseTransport | None = None,
    ):
        self._config = _Config(
            app, version, contact, username=username, password=password, oauth_token=oauth_token, auth=auth,
            base_url=base_url, max_retries=max_retries, backoff_factor=backoff_factor, validate=validate,
        )
        self._limiter = rate_limiter or (RateLimiter(rate_limit) if rate_limit else None)
        self._http = httpx2.Client(
            base_url=self._config.base_url, headers=self._config.headers, auth=self._config.auth,
            timeout=timeout, transport=transport, follow_redirects=True,
        )
        self.areas = EntityResource[M.Area](self, "area")
        self.artists = EntityResource[M.Artist](self, "artist")
        self.events = EntityResource[M.Event](self, "event")
        self.genres = GenreResource(self, "genre")
        self.instruments = EntityResource[M.Instrument](self, "instrument")
        self.labels = EntityResource[M.Label](self, "label")
        self.places = EntityResource[M.Place](self, "place")
        self.recordings = EntityResource[M.Recording](self, "recording")
        self.releases = EntityResource[M.Release](self, "release")
        self.release_groups = EntityResource[M.ReleaseGroup](self, "release-group")
        self.series = EntityResource[M.Series](self, "series")
        self.works = EntityResource[M.Work](self, "work")
        self.urls = UrlResource(self, "url")
        self.collections = CollectionResource(self, "collection")
        self.annotations = SearchOnlyResource[M.Annotation](self, "annotation")
        self.cdstubs = SearchOnlyResource[M.CDStub](self, "cdstub")
        self.tags = SearchOnlyResource[M.Tag](self, "tag")

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def user_agent(self) -> str:
        return self._config.user_agent

    def _resource(self, entity: str) -> EntityResource[Any]:
        entity = {v: k for k, v in C.COLLECTION_ENTITY_TYPES.items()}.get(entity, entity)
        res = getattr(self, C.PLURALS.get(entity, "").replace("-", "_"), None)
        if not isinstance(res, EntityResource):
            raise ValidationError(f"unknown entity type: {entity!r}")
        return res

    # ------------------------------------------------------------------ non-MBID lookups

    def lookup_discid(self, discid: str, *, inc: Includes = None, toc: str | Iterable[int] | None = None,
                      cdstubs: bool = True, all_media_formats: bool = False, release_type: Filter = None,
                      release_status: Filter = None) -> M.Disc | M.CDStub:
        """Releases for a disc ID. Pass ``toc`` for fuzzy matching (use ``discid="-"`` for TOC-only).

        Returns a :class:`Disc` (with ``releases``) or, if only a CD stub matches, a :class:`CDStub`.
        """
        return self._run(self._config.core.lookup_discid(
            discid, inc, toc=toc, cdstubs=cdstubs, all_media_formats=all_media_formats,
            release_type=release_type, release_status=release_status))

    def lookup_isrc(self, isrc: str, *, inc: Includes = None) -> M.IsrcResult:
        """Recordings with the given ISRC."""
        return self._run(self._config.core.lookup_isrc(isrc, inc))

    def lookup_iswc(self, iswc: str, *, inc: Includes = None) -> list[M.Work]:
        """Works with the given ISWC."""
        return self._run(self._config.core.lookup_iswc(iswc, inc))

    # ------------------------------------------------------------------ submissions (auth required)

    def submit_tags(self, entity: str, tags: Mapping[str, Iterable[str]], *, vote: Vote | None = None) -> None:
        """Tag entities: ``submit_tags("artist", {mbid: ["rock", "grunge"]}, vote="upvote")``.

        Without ``vote``, the given tags *replace* all your upvoted tags on each entity.
        """
        self._run(self._config.core.submit_tags(entity, tags, vote))

    def submit_ratings(self, entity: str, ratings: Mapping[str, int]) -> None:
        """Rate entities 0-100 (20 per star; 0 removes your rating)."""
        self._run(self._config.core.submit_ratings(entity, ratings))

    def submit_barcodes(self, barcodes: Mapping[str, str]) -> None:
        """Add barcodes to releases (``{release_mbid: "GTIN"}``). Creates edits."""
        self._run(self._config.core.submit_barcodes(barcodes))

    def submit_isrcs(self, isrcs: Mapping[str, Iterable[str]]) -> None:
        """Add ISRCs to recordings (``{recording_mbid: ["USxxx..."]}``)."""
        self._run(self._config.core.submit_isrcs(isrcs))

    # ------------------------------------------------------------------ low level

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        """Escape hatch: perform a raw JSON request and return the decoded body."""
        return self._run(Call(method, path.lstrip("/"), list((params or {}).items()), lambda d: d))

    def _run(self, call: Call[R]) -> R:
        cfg = self._config
        cfg.check_auth(call)
        for attempt in range(cfg.max_retries + 1):
            if self._limiter:
                self._limiter.acquire()
            try:
                resp = self._http.request(call.method, call.path, params=call.request_params(),
                                          content=call.content, headers=call.request_headers())
            except httpx2.TransportError as exc:
                if attempt < cfg.max_retries and _retryable_exc(call.method, exc):
                    time.sleep(cfg.backoff_factor * 2**attempt)
                    continue
                raise NetworkError(str(exc) or type(exc).__name__) from exc
            if attempt < cfg.max_retries:
                delay = retry_delay(resp.status_code, resp.headers.get("Retry-After"), attempt, cfg.backoff_factor)
                if delay is not None:
                    time.sleep(delay)
                    continue
            raise_for_response(resp.status_code, resp.content, str(resp.url), resp.reason_phrase)
            return call.parse(decode_body(call, resp.content))
        raise AssertionError("unreachable")

    def _paginate(self, make: Callable[[int], Call[M.Page[R]]], max_items: int | None) -> Iterator[R]:
        first = make(0)  # build eagerly so invalid arguments raise immediately
        return self._pages(first, make, max_items)

    def _pages(self, first: Call[M.Page[R]], make: Callable[[int], Call[M.Page[R]]],
               max_items: int | None) -> Iterator[R]:
        offset, seen, call = 0, 0, first
        while True:
            page = self._run(call)
            for item in page.items:
                yield item
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            if not page.has_more:
                return
            # Release browse pages may contain fewer than `limit` items: advance by what we got.
            offset = page.next_offset
            call = make(offset)


# =========================================================================== async


class AsyncEntityResource(Generic[T]):
    """Async counterpart of :class:`EntityResource`."""

    def __init__(self, client: AsyncMusicBrainz, entity: str):
        self._client = client
        self._core = client._config.core
        self.entity = entity

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.entity}>"

    async def get(self, mbid: str, *, inc: Includes = None, release_type: Filter = None,
                  release_status: Filter = None) -> T:
        return await self._client._run(self._core.lookup(self.entity, mbid, inc, release_type, release_status))

    async def browse(self, *, inc: Includes = None, limit: int | None = None, offset: int | None = None,
                     release_type: Filter = None, release_status: Filter = None,
                     release_group_status: str | None = None, **linked: str) -> M.Page[T]:
        return await self._client._run(self._core.browse(
            self.entity, linked, inc, limit, offset, release_type, release_status, release_group_status))

    def iter_browse(self, *, inc: Includes = None, page_size: int = C.MAX_LIMIT, max_items: int | None = None,
                    release_type: Filter = None, release_status: Filter = None,
                    release_group_status: str | None = None, **linked: str) -> AsyncIterator[T]:
        return self._client._paginate(
            lambda off: self._core.browse(self.entity, linked, inc, page_size, off, release_type, release_status,
                                          release_group_status),
            max_items,
        )

    async def search(self, query: str | None = None, *, limit: int | None = None, offset: int | None = None,
                     dismax: bool = False, **fields: object) -> M.Page[T]:
        return await self._client._run(self._core.search(self.entity, query, fields, limit, offset, dismax))

    def iter_search(self, query: str | None = None, *, page_size: int = C.MAX_LIMIT, max_items: int | None = None,
                    dismax: bool = False, **fields: object) -> AsyncIterator[T]:
        return self._client._paginate(
            lambda off: self._core.search(self.entity, query, fields, page_size, off, dismax), max_items)


class AsyncSearchOnlyResource(Generic[T]):
    def __init__(self, client: AsyncMusicBrainz, entity: str):
        self._client = client
        self._core = client._config.core
        self.entity = entity

    search = AsyncEntityResource.search
    iter_search = AsyncEntityResource.iter_search


class AsyncUrlResource(AsyncEntityResource[M.Url]):
    async def by_resource(self, url: str, *, inc: Includes = None) -> M.Url:
        return (await self._client._run(self._core.lookup_urls([url], inc)))[0]

    async def by_resources(self, urls: Iterable[str], *, inc: Includes = None) -> list[M.Url]:
        result: list[M.Url] = []
        for chunk in _chunks(list(urls), C.URL_BATCH_SIZE):
            try:
                result += await self._client._run(self._core.lookup_urls(chunk, inc))
            except NotFoundError:
                if len(chunk) > 1:
                    raise
        return result


class AsyncGenreResource(AsyncEntityResource[M.Genre]):
    async def all(self, *, limit: int | None = None, offset: int | None = None) -> M.Page[M.Genre]:
        return await self._client._run(self._core.genres_all(limit, offset))

    def iter_all(self, *, page_size: int = C.MAX_LIMIT, max_items: int | None = None) -> AsyncIterator[M.Genre]:
        return self._client._paginate(lambda off: self._core.genres_all(page_size, off), max_items)

    async def names(self) -> list[str]:
        return await self._client._run(self._core.genre_names())


class AsyncCollectionResource(AsyncEntityResource[M.Collection]):
    async def of_editor(self, editor: str, *, include_private: bool = False, limit: int | None = None,
                        offset: int | None = None) -> M.Page[M.Collection]:
        return await self.browse(editor=editor, inc=["user-collections"] if include_private else None,
                                 limit=limit, offset=offset)

    async def summary(self, mbid: str, entity_type: str) -> M.Collection:
        return await self._client._run(self._core.lookup_collection_contents(mbid, entity_type))

    async def items(self, mbid: str, entity_type: str, **kwargs: Any) -> M.Page[Any]:
        return await self._client._resource(entity_type).browse(collection=mbid, **kwargs)

    def iter_items(self, mbid: str, entity_type: str, **kwargs: Any) -> AsyncIterator[Any]:
        return self._client._resource(entity_type).iter_browse(collection=mbid, **kwargs)

    async def add(self, mbid: str, entity_type: str, mbids: Iterable[str]) -> None:
        for chunk in _chunks(list(mbids), C.COLLECTION_BATCH_SIZE):
            await self._client._run(self._core.collection_modify("PUT", mbid, entity_type, chunk))

    async def remove(self, mbid: str, entity_type: str, mbids: Iterable[str]) -> None:
        for chunk in _chunks(list(mbids), C.COLLECTION_BATCH_SIZE):
            await self._client._run(self._core.collection_modify("DELETE", mbid, entity_type, chunk))


class AsyncMusicBrainz:
    """Asynchronous MusicBrainz WS/2 client (asyncio or trio). Same API as :class:`MusicBrainz`, awaited.

    >>> async with AsyncMusicBrainz("MyApp", "1.0", "me@example.com") as mb:
    ...     page = await mb.artists.search("Nirvana")
    ...     async for rg in mb.release_groups.iter_browse(artist=page[0].id):
    ...         print(rg.title)
    """

    def __init__(
        self,
        app: str,
        version: str,
        contact: str | None = None,
        *,
        username: str | None = None,
        password: str | None = None,
        oauth_token: str | None = None,
        auth: httpx2.Auth | None = None,
        base_url: str = C.DEFAULT_BASE_URL,
        rate_limit: float | None = 1.0,
        rate_limiter: AsyncRateLimiter | None = None,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        timeout: float = 30.0,
        validate: bool = True,
        transport: httpx2.AsyncBaseTransport | None = None,
    ):
        self._config = _Config(
            app, version, contact, username=username, password=password, oauth_token=oauth_token, auth=auth,
            base_url=base_url, max_retries=max_retries, backoff_factor=backoff_factor, validate=validate,
        )
        self._limiter = rate_limiter or (AsyncRateLimiter(rate_limit) if rate_limit else None)
        self._http = httpx2.AsyncClient(
            base_url=self._config.base_url, headers=self._config.headers, auth=self._config.auth,
            timeout=timeout, transport=transport, follow_redirects=True,
        )
        self.areas = AsyncEntityResource[M.Area](self, "area")
        self.artists = AsyncEntityResource[M.Artist](self, "artist")
        self.events = AsyncEntityResource[M.Event](self, "event")
        self.genres = AsyncGenreResource(self, "genre")
        self.instruments = AsyncEntityResource[M.Instrument](self, "instrument")
        self.labels = AsyncEntityResource[M.Label](self, "label")
        self.places = AsyncEntityResource[M.Place](self, "place")
        self.recordings = AsyncEntityResource[M.Recording](self, "recording")
        self.releases = AsyncEntityResource[M.Release](self, "release")
        self.release_groups = AsyncEntityResource[M.ReleaseGroup](self, "release-group")
        self.series = AsyncEntityResource[M.Series](self, "series")
        self.works = AsyncEntityResource[M.Work](self, "work")
        self.urls = AsyncUrlResource(self, "url")
        self.collections = AsyncCollectionResource(self, "collection")
        self.annotations = AsyncSearchOnlyResource[M.Annotation](self, "annotation")
        self.cdstubs = AsyncSearchOnlyResource[M.CDStub](self, "cdstub")
        self.tags = AsyncSearchOnlyResource[M.Tag](self, "tag")

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    @property
    def user_agent(self) -> str:
        return self._config.user_agent

    def _resource(self, entity: str) -> AsyncEntityResource[Any]:
        entity = {v: k for k, v in C.COLLECTION_ENTITY_TYPES.items()}.get(entity, entity)
        res = getattr(self, C.PLURALS.get(entity, "").replace("-", "_"), None)
        if not isinstance(res, AsyncEntityResource):
            raise ValidationError(f"unknown entity type: {entity!r}")
        return res

    async def lookup_discid(self, discid: str, *, inc: Includes = None, toc: str | Iterable[int] | None = None,
                            cdstubs: bool = True, all_media_formats: bool = False, release_type: Filter = None,
                            release_status: Filter = None) -> M.Disc | M.CDStub:
        return await self._run(self._config.core.lookup_discid(
            discid, inc, toc=toc, cdstubs=cdstubs, all_media_formats=all_media_formats,
            release_type=release_type, release_status=release_status))

    async def lookup_isrc(self, isrc: str, *, inc: Includes = None) -> M.IsrcResult:
        return await self._run(self._config.core.lookup_isrc(isrc, inc))

    async def lookup_iswc(self, iswc: str, *, inc: Includes = None) -> list[M.Work]:
        return await self._run(self._config.core.lookup_iswc(iswc, inc))

    async def submit_tags(self, entity: str, tags: Mapping[str, Iterable[str]], *, vote: Vote | None = None) -> None:
        await self._run(self._config.core.submit_tags(entity, tags, vote))

    async def submit_ratings(self, entity: str, ratings: Mapping[str, int]) -> None:
        await self._run(self._config.core.submit_ratings(entity, ratings))

    async def submit_barcodes(self, barcodes: Mapping[str, str]) -> None:
        await self._run(self._config.core.submit_barcodes(barcodes))

    async def submit_isrcs(self, isrcs: Mapping[str, Iterable[str]]) -> None:
        await self._run(self._config.core.submit_isrcs(isrcs))

    async def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        return await self._run(Call(method, path.lstrip("/"), list((params or {}).items()), lambda d: d))

    async def _run(self, call: Call[R]) -> R:
        cfg = self._config
        cfg.check_auth(call)
        for attempt in range(cfg.max_retries + 1):
            if self._limiter:
                await self._limiter.acquire()
            try:
                resp = await self._http.request(call.method, call.path, params=call.request_params(),
                                                content=call.content, headers=call.request_headers())
            except httpx2.TransportError as exc:
                if attempt < cfg.max_retries and _retryable_exc(call.method, exc):
                    await anyio.sleep(cfg.backoff_factor * 2**attempt)
                    continue
                raise NetworkError(str(exc) or type(exc).__name__) from exc
            if attempt < cfg.max_retries:
                delay = retry_delay(resp.status_code, resp.headers.get("Retry-After"), attempt, cfg.backoff_factor)
                if delay is not None:
                    await anyio.sleep(delay)
                    continue
            raise_for_response(resp.status_code, resp.content, str(resp.url), resp.reason_phrase)
            return call.parse(decode_body(call, resp.content))
        raise AssertionError("unreachable")

    def _paginate(self, make: Callable[[int], Call[M.Page[R]]], max_items: int | None) -> AsyncIterator[R]:
        first = make(0)  # build eagerly so invalid arguments raise immediately
        return self._pages(first, make, max_items)

    async def _pages(self, first: Call[M.Page[R]], make: Callable[[int], Call[M.Page[R]]],
                     max_items: int | None) -> AsyncIterator[R]:
        offset, seen, call = 0, 0, first
        while True:
            page = await self._run(call)
            for item in page.items:
                yield item
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            if not page.has_more:
                return
            offset = page.next_offset
            call = make(offset)
