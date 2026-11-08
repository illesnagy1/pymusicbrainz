"""Synchronous and asynchronous MusicBrainz clients."""

import logging
import time
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping
from typing import Any, Literal, Self, TypedDict, Unpack

import anyio
import httpx2

from . import _constants as consts
from . import models
from ._core import (
    JSON,
    Call,
    Core,
    Filter,
    Includes,
    decode_body,
    raise_for_response,
    retry_delay,
)
from ._ratelimit import AsyncRateLimiter, RateLimiter
from ._version import __version__
from .errors import (
    AuthenticationRequiredError,
    NetworkError,
    NotFoundError,
    ValidationError,
)

Vote = Literal["upvote", "downvote", "withdraw"]

logger = logging.getLogger(__name__)


class BrowseOptions(TypedDict, total=False, closed=True):
    """Keyword arguments accepted by ``browse()`` besides the linked entity."""

    inc: Includes
    limit: int | None
    offset: int | None
    release_type: Filter
    release_status: Filter
    release_group_status: str | None


class IterBrowseOptions(TypedDict, total=False, closed=True):
    """Keyword arguments accepted by ``iter_browse()`` besides the linked entity."""

    inc: Includes
    page_size: int
    max_items: int | None
    release_type: Filter
    release_status: Filter
    release_group_status: str | None


# ================================================================== shared config


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
    ) -> None:
        if not app or not version:
            raise ValidationError(
                "app and version are required to build a meaningful User-Agent"
            )
        self.user_agent = (
            f"{app}/{version}"
            + (f" ( {contact} )" if contact else "")
            + f" python-musicbrainz/{__version__}"
        )
        if sum(x is not None for x in (password, oauth_token, auth)) > 1:
            raise ValidationError(
                "pass only one of username/password, oauth_token or auth"
            )
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
            raise AuthenticationRequiredError(
                f"{call.method} {call.path} requires authentication"
            )


def _retryable_exc(method: str, exc: Exception) -> bool:
    # Never replay a submission whose request may already have reached the server.
    if method == "GET":
        return isinstance(exc, httpx2.TransportError)
    return isinstance(exc, (httpx2.ConnectError, httpx2.ConnectTimeout))


def _log_response(call: Call[Any], resp: httpx2.Response, started: float) -> None:
    logger.debug(
        "%s %s -> %d %s (%.0f ms)",
        call.method,
        resp.url,
        resp.status_code,
        resp.reason_phrase,
        (time.perf_counter() - started) * 1000,
    )


def _log_retry(
    call: Call[Any], reason: str, attempt: int, max_retries: int, delay: float
) -> None:
    logger.warning(
        "%s %s failed (%s); retry %d/%d in %.1fs",
        call.method,
        call.path,
        reason,
        attempt + 1,
        max_retries,
        delay,
    )


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _resource_key(entity: str) -> str:
    """Normalize ``"releases"`` etc. to the singular entity name."""
    plural_to_singular = {v: k for k, v in consts.COLLECTION_ENTITY_TYPES.items()}
    return plural_to_singular.get(entity, entity)


def _raw_call(method: str, path: str, params: Mapping[str, str] | None) -> Call[JSON]:
    return Call(method, path.lstrip("/"), list((params or {}).items()), lambda d: d)


# ================================================================== sync


class _Session:
    """Executes `Call` objects: rate limiting, retries, error mapping."""

    def __init__(
        self,
        config: _Config,
        limiter: RateLimiter | None,
        http: httpx2.Client,
    ) -> None:
        self.config = config
        self.core = config.core
        self.limiter = limiter
        self.http = http
        self.resources: dict[str, EntityResource[Any]] = {}

    def resource(self, entity: str) -> EntityResource[Any]:
        try:
            return self.resources[_resource_key(entity)]
        except KeyError:
            raise ValidationError(f"unknown entity type: {entity!r}") from None

    def run[R](self, call: Call[R]) -> R:
        cfg = self.config
        cfg.check_auth(call)
        for attempt in range(cfg.max_retries + 1):
            if self.limiter:
                self.limiter.acquire()
            started = time.perf_counter()
            try:
                resp = self.http.request(
                    call.method,
                    call.path,
                    params=call.request_params(),
                    content=call.content,
                    headers=call.request_headers(),
                )
            except httpx2.TransportError as exc:
                if attempt < cfg.max_retries and _retryable_exc(call.method, exc):
                    delay = cfg.backoff_factor * 2**attempt
                    _log_retry(call, repr(exc), attempt, cfg.max_retries, delay)
                    time.sleep(delay)
                    continue
                raise NetworkError(str(exc) or type(exc).__name__) from exc
            _log_response(call, resp, started)
            if attempt < cfg.max_retries:
                delay = retry_delay(
                    resp.status_code,
                    resp.headers.get("Retry-After"),
                    attempt,
                    cfg.backoff_factor,
                )
                if delay is not None:
                    reason = f"HTTP {resp.status_code}"
                    _log_retry(call, reason, attempt, cfg.max_retries, delay)
                    time.sleep(delay)
                    continue
            raise_for_response(
                resp.status_code, resp.content, str(resp.url), resp.reason_phrase
            )
            return call.parse(decode_body(call, resp.content))
        raise AssertionError("unreachable")

    def paginate[R](
        self,
        make: Callable[[int], Call[models.Page[R]]],
        max_items: int | None,
    ) -> Iterator[R]:
        first = make(0)  # build eagerly so invalid arguments raise immediately
        return self._pages(first, make, max_items)

    def _pages[R](
        self,
        first: Call[models.Page[R]],
        make: Callable[[int], Call[models.Page[R]]],
        max_items: int | None,
    ) -> Iterator[R]:
        seen, call = 0, first
        while True:
            page = self.run(call)
            for item in page.items:
                yield item
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            if not page.has_more:
                return
            # Release pages may hold fewer than `limit` items: advance by what we got.
            call = make(page.next_offset)


class EntityResource[T]:
    """Lookup / browse / search for one entity type, e.g. ``mb.artists``."""

    def __init__(self, session: _Session, entity: str) -> None:
        self._session = session
        self._core = session.core
        self.entity = entity
        session.resources[entity] = self

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.entity}>"

    def get(
        self,
        mbid: str,
        *,
        inc: Includes = None,
        release_type: Filter = None,
        release_status: Filter = None,
    ) -> T:
        """Look up one entity by MBID.

        ``inc`` takes a list (or ``+``/space separated string) of includes, e.g.
        ``["aliases", "url-rels"]``. ``release_type``/``release_status`` filter
        included releases / release groups (``"album"``, ``["album", "ep"]``,
        ``"official"``...).
        """
        return self._session.run(
            self._core.lookup(self.entity, mbid, inc, release_type, release_status)
        )

    def browse(
        self,
        *,
        inc: Includes = None,
        limit: int | None = None,
        offset: int | None = None,
        release_type: Filter = None,
        release_status: Filter = None,
        release_group_status: str | None = None,
        **linked: str,
    ) -> models.Page[T]:
        """Browse entities linked to another entity.

        For example ``mb.releases.browse(artist=mbid)``. Use ``release_group=...``
        for ``release-group``; ``track_artist=...`` is passed as-is.
        """
        return self._session.run(
            self._core.browse(
                self.entity,
                linked,
                inc,
                limit,
                offset,
                release_type,
                release_status,
                release_group_status,
            )
        )

    def iter_browse(
        self,
        *,
        inc: Includes = None,
        page_size: int = consts.MAX_LIMIT,
        max_items: int | None = None,
        release_type: Filter = None,
        release_status: Filter = None,
        release_group_status: str | None = None,
        **linked: str,
    ) -> Iterator[T]:
        """Iterate over all browse results, fetching pages as needed."""
        return self._session.paginate(
            lambda off: self._core.browse(
                self.entity,
                linked,
                inc,
                page_size,
                off,
                release_type,
                release_status,
                release_group_status,
            ),
            max_items,
        )

    def search(
        self,
        query: str | None = None,
        *,
        limit: int | None = None,
        offset: int | None = None,
        dismax: bool = False,
        **fields: object,
    ) -> models.Page[T]:
        """Search, e.g. ``mb.artists.search("nirvana", country="US")``.

        ``query`` is raw Lucene syntax; ``fields`` are added as ``field:"value"``
        terms joined by AND. Each result has a ``score`` (0-100).
        """
        return self._session.run(
            self._core.search(self.entity, query, fields, limit, offset, dismax)
        )

    def iter_search(
        self,
        query: str | None = None,
        *,
        page_size: int = consts.MAX_LIMIT,
        max_items: int | None = None,
        dismax: bool = False,
        **fields: object,
    ) -> Iterator[T]:
        """Iterate over search results. Use ``max_items`` to stop early."""
        return self._session.paginate(
            lambda off: self._core.search(
                self.entity, query, fields, page_size, off, dismax
            ),
            max_items,
        )


class SearchOnlyResource[T]:
    """Entities that can only be searched (annotation, cdstub, tag)."""

    def __init__(self, session: _Session, entity: str) -> None:
        self._session = session
        self._core = session.core
        self.entity = entity

    search = EntityResource.search
    iter_search = EntityResource.iter_search


class UrlResource(EntityResource[models.Url]):
    def by_resource(self, url: str, *, inc: Includes = None) -> models.Url:
        """Look up a URL entity by its text.

        Raises:
            NotFoundError: If MusicBrainz doesn't know the URL.
        """
        return self._session.run(self._core.lookup_urls([url], inc))[0]

    def by_resources(
        self, urls: Iterable[str], *, inc: Includes = None
    ) -> list[models.Url]:
        """Look up many URLs (batched by 100). Unknown URLs are silently skipped."""
        result: list[models.Url] = []
        for chunk in _chunks(list(urls), consts.URL_BATCH_SIZE):
            try:
                result += self._session.run(self._core.lookup_urls(chunk, inc))
            except NotFoundError:
                if len(chunk) > 1:
                    raise
        return result


class GenreResource(EntityResource[models.Genre]):
    def all(
        self, *, limit: int | None = None, offset: int | None = None
    ) -> models.Page[models.Genre]:
        """One page of the alphabetical list of all genres."""
        return self._session.run(self._core.genres_all(limit, offset))

    def iter_all(
        self, *, page_size: int = consts.MAX_LIMIT, max_items: int | None = None
    ) -> Iterator[models.Genre]:
        """Iterate over all genres."""
        return self._session.paginate(
            lambda off: self._core.genres_all(page_size, off), max_items
        )

    def names(self) -> list[str]:
        """All genre names (single plain-text request)."""
        return self._session.run(self._core.genre_names())


class CollectionResource(EntityResource[models.Collection]):
    def of_editor(
        self,
        editor: str,
        *,
        include_private: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> models.Page[models.Collection]:
        """Collections of an editor.

        ``include_private`` requires authenticating as that editor.
        """
        return self.browse(
            editor=editor,
            inc=["user-collections"] if include_private else None,
            limit=limit,
            offset=offset,
        )

    def summary(self, mbid: str, entity_type: str) -> models.Collection:
        """Collection description with a summary of its ``entity_type`` contents."""
        return self._session.run(
            self._core.lookup_collection_contents(mbid, entity_type)
        )

    def items(
        self, mbid: str, entity_type: str, **options: Unpack[BrowseOptions]
    ) -> models.Page[Any]:
        """One page of collection contents, e.g. ``items(mbid, "release")``."""
        return self._session.resource(entity_type).browse(collection=mbid, **options)

    def iter_items(
        self, mbid: str, entity_type: str, **options: Unpack[IterBrowseOptions]
    ) -> Iterator[Any]:
        """Iterate over all collection contents."""
        return self._session.resource(entity_type).iter_browse(
            collection=mbid, **options
        )

    def add(self, mbid: str, entity_type: str, mbids: Iterable[str]) -> None:
        """Add entities to a collection you own (batched automatically)."""
        for chunk in _chunks(list(mbids), consts.COLLECTION_BATCH_SIZE):
            self._session.run(
                self._core.collection_modify("PUT", mbid, entity_type, chunk)
            )

    def remove(self, mbid: str, entity_type: str, mbids: Iterable[str]) -> None:
        """Remove entities from a collection you own (batched automatically)."""
        for chunk in _chunks(list(mbids), consts.COLLECTION_BATCH_SIZE):
            self._session.run(
                self._core.collection_modify("DELETE", mbid, entity_type, chunk)
            )


class MusicBrainz:
    """Synchronous MusicBrainz WS/2 client.

    Entity namespaces (`areas`, `artists`, `release_groups`, ...) are
    [`EntityResource`][musicbrainz.client.EntityResource] instances; see the
    [API overview](../guide/lookups.md) for what they can do.

    Args:
        app: Your application's name, used in the User-Agent and as the
            submission client ID.
        version: Your application's version. It must not contain a hyphen if
            you want to submit data.
        contact: URL or e-mail address where MusicBrainz admins can reach you.
        username: Account name for digest authentication.
        password: A token from your MusicBrainz *Applications* page (not your
            account password), used with `username`.
        oauth_token: OAuth2 bearer token, an alternative to digest auth.
        auth: Any other `httpx2.Auth` implementation.
        base_url: API root, e.g. a private mirror.
        rate_limit: Maximum requests per second; `None` disables limiting.
        rate_limiter: A [`RateLimiter`][musicbrainz.RateLimiter] shared with
            other clients; overrides `rate_limit`.
        max_retries: Retries on 429/502/503/504 responses and network errors.
        backoff_factor: Base of the exponential back-off, in seconds.
        timeout: Per-request timeout in seconds.
        validate: Check includes and filter values client-side before sending.
        transport: Custom `httpx2` transport, e.g. `httpx2.MockTransport` in
            tests.

    Raises:
        ValidationError: If `app`/`version` are empty or the auth options
            conflict.

    Example:
        ```python
        with MusicBrainz("MyApp", "1.0", "me@example.com") as mb:
            artist = mb.artists.get(
                "5b11f4ce-a62d-471e-81fc-a69a8278c7da", inc=["aliases"]
            )
        ```
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
        base_url: str = consts.DEFAULT_BASE_URL,
        rate_limit: float | None = 1.0,
        rate_limiter: RateLimiter | None = None,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        timeout: float = 30.0,
        validate: bool = True,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        config = _Config(
            app,
            version,
            contact,
            username=username,
            password=password,
            oauth_token=oauth_token,
            auth=auth,
            base_url=base_url,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            validate=validate,
        )
        http = httpx2.Client(
            base_url=config.base_url,
            headers=config.headers,
            auth=config.auth,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )
        limiter = rate_limiter or (RateLimiter(rate_limit) if rate_limit else None)
        self._session = s = _Session(config, limiter, http)
        self._core = config.core
        self.user_agent = config.user_agent

        self.areas = EntityResource[models.Area](s, "area")
        self.artists = EntityResource[models.Artist](s, "artist")
        self.events = EntityResource[models.Event](s, "event")
        self.genres = GenreResource(s, "genre")
        self.instruments = EntityResource[models.Instrument](s, "instrument")
        self.labels = EntityResource[models.Label](s, "label")
        self.places = EntityResource[models.Place](s, "place")
        self.recordings = EntityResource[models.Recording](s, "recording")
        self.releases = EntityResource[models.Release](s, "release")
        self.release_groups = EntityResource[models.ReleaseGroup](s, "release-group")
        self.series = EntityResource[models.Series](s, "series")
        self.works = EntityResource[models.Work](s, "work")
        self.urls = UrlResource(s, "url")
        self.collections = CollectionResource(s, "collection")
        self.annotations = SearchOnlyResource[models.Annotation](s, "annotation")
        self.cdstubs = SearchOnlyResource[models.CDStub](s, "cdstub")
        self.tags = SearchOnlyResource[models.Tag](s, "tag")

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._session.http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def resource(self, entity: str) -> EntityResource[Any]:
        """The resource for an entity name, e.g. ``"release-group"``."""
        return self._session.resource(entity)

    # ------------------------------------------------------------ non-MBID lookups

    def lookup_discid(
        self,
        discid: str,
        *,
        inc: Includes = None,
        toc: str | Iterable[int] | None = None,
        cdstubs: bool = True,
        all_media_formats: bool = False,
        release_type: Filter = None,
        release_status: Filter = None,
    ) -> models.Disc | models.CDStub:
        """Releases for a disc ID.

        Pass ``toc`` for fuzzy matching (use ``discid="-"`` for TOC-only).
        Returns a [`Disc`][musicbrainz.models.Disc] (with ``releases``) or,
        if only a CD stub matches, a [`CDStub`][musicbrainz.models.CDStub].
        """
        return self._session.run(
            self._core.lookup_discid(
                discid,
                inc,
                toc=toc,
                cdstubs=cdstubs,
                all_media_formats=all_media_formats,
                release_type=release_type,
                release_status=release_status,
            )
        )

    def lookup_isrc(self, isrc: str, *, inc: Includes = None) -> models.IsrcResult:
        """Recordings with the given ISRC."""
        return self._session.run(self._core.lookup_isrc(isrc, inc))

    def lookup_iswc(self, iswc: str, *, inc: Includes = None) -> list[models.Work]:
        """Works with the given ISWC."""
        return self._session.run(self._core.lookup_iswc(iswc, inc))

    # ------------------------------------------------------------ submissions

    def submit_tags(
        self,
        entity: str,
        tags: Mapping[str, Iterable[str]],
        *,
        vote: Vote | None = None,
    ) -> None:
        """Tag entities, e.g. ``submit_tags("artist", {mbid: ["rock"]})``.

        Without ``vote``, the given tags *replace* all your upvoted tags on each
        entity.
        """
        self._session.run(self._core.submit_tags(entity, tags, vote))

    def submit_ratings(self, entity: str, ratings: Mapping[str, int]) -> None:
        """Rate entities 0-100 (20 per star; 0 removes your rating)."""
        self._session.run(self._core.submit_ratings(entity, ratings))

    def submit_barcodes(self, barcodes: Mapping[str, str]) -> None:
        """Add barcodes to releases (``{release_mbid: "GTIN"}``). Creates edits."""
        self._session.run(self._core.submit_barcodes(barcodes))

    def submit_isrcs(self, isrcs: Mapping[str, Iterable[str]]) -> None:
        """Add ISRCs to recordings (``{recording_mbid: ["USxxx..."]}``)."""
        self._session.run(self._core.submit_isrcs(isrcs))

    # ------------------------------------------------------------ low level

    def request(
        self, method: str, path: str, params: Mapping[str, str] | None = None
    ) -> JSON:
        """Escape hatch: perform a raw JSON request and return the decoded body."""
        return self._session.run(_raw_call(method, path, params))


# ================================================================== async


class _AsyncSession:
    """Async counterpart of `_Session`."""

    def __init__(
        self,
        config: _Config,
        limiter: AsyncRateLimiter | None,
        http: httpx2.AsyncClient,
    ) -> None:
        self.config = config
        self.core = config.core
        self.limiter = limiter
        self.http = http
        self.resources: dict[str, AsyncEntityResource[Any]] = {}

    def resource(self, entity: str) -> AsyncEntityResource[Any]:
        try:
            return self.resources[_resource_key(entity)]
        except KeyError:
            raise ValidationError(f"unknown entity type: {entity!r}") from None

    async def run[R](self, call: Call[R]) -> R:
        cfg = self.config
        cfg.check_auth(call)
        for attempt in range(cfg.max_retries + 1):
            if self.limiter:
                await self.limiter.acquire()
            started = time.perf_counter()
            try:
                resp = await self.http.request(
                    call.method,
                    call.path,
                    params=call.request_params(),
                    content=call.content,
                    headers=call.request_headers(),
                )
            except httpx2.TransportError as exc:
                if attempt < cfg.max_retries and _retryable_exc(call.method, exc):
                    delay = cfg.backoff_factor * 2**attempt
                    _log_retry(call, repr(exc), attempt, cfg.max_retries, delay)
                    await anyio.sleep(delay)
                    continue
                raise NetworkError(str(exc) or type(exc).__name__) from exc
            _log_response(call, resp, started)
            if attempt < cfg.max_retries:
                delay = retry_delay(
                    resp.status_code,
                    resp.headers.get("Retry-After"),
                    attempt,
                    cfg.backoff_factor,
                )
                if delay is not None:
                    reason = f"HTTP {resp.status_code}"
                    _log_retry(call, reason, attempt, cfg.max_retries, delay)
                    await anyio.sleep(delay)
                    continue
            raise_for_response(
                resp.status_code, resp.content, str(resp.url), resp.reason_phrase
            )
            return call.parse(decode_body(call, resp.content))
        raise AssertionError("unreachable")

    def paginate[R](
        self,
        make: Callable[[int], Call[models.Page[R]]],
        max_items: int | None,
    ) -> AsyncIterator[R]:
        first = make(0)  # build eagerly so invalid arguments raise immediately
        return self._pages(first, make, max_items)

    async def _pages[R](
        self,
        first: Call[models.Page[R]],
        make: Callable[[int], Call[models.Page[R]]],
        max_items: int | None,
    ) -> AsyncIterator[R]:
        seen, call = 0, first
        while True:
            page = await self.run(call)
            for item in page.items:
                yield item
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            if not page.has_more:
                return
            call = make(page.next_offset)


class AsyncEntityResource[T]:
    """Async counterpart of [`EntityResource`][musicbrainz.client.EntityResource]."""

    def __init__(self, session: _AsyncSession, entity: str) -> None:
        self._session = session
        self._core = session.core
        self.entity = entity
        session.resources[entity] = self

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.entity}>"

    async def get(
        self,
        mbid: str,
        *,
        inc: Includes = None,
        release_type: Filter = None,
        release_status: Filter = None,
    ) -> T:
        """Look up one entity by MBID.

        See [`EntityResource.get`][musicbrainz.client.EntityResource.get].
        """
        return await self._session.run(
            self._core.lookup(self.entity, mbid, inc, release_type, release_status)
        )

    async def browse(
        self,
        *,
        inc: Includes = None,
        limit: int | None = None,
        offset: int | None = None,
        release_type: Filter = None,
        release_status: Filter = None,
        release_group_status: str | None = None,
        **linked: str,
    ) -> models.Page[T]:
        """Browse linked entities.

        See
        [`EntityResource.browse`][musicbrainz.client.EntityResource.browse].
        """
        return await self._session.run(
            self._core.browse(
                self.entity,
                linked,
                inc,
                limit,
                offset,
                release_type,
                release_status,
                release_group_status,
            )
        )

    def iter_browse(
        self,
        *,
        inc: Includes = None,
        page_size: int = consts.MAX_LIMIT,
        max_items: int | None = None,
        release_type: Filter = None,
        release_status: Filter = None,
        release_group_status: str | None = None,
        **linked: str,
    ) -> AsyncIterator[T]:
        """Iterate over all browse results, fetching pages as needed."""
        return self._session.paginate(
            lambda off: self._core.browse(
                self.entity,
                linked,
                inc,
                page_size,
                off,
                release_type,
                release_status,
                release_group_status,
            ),
            max_items,
        )

    async def search(
        self,
        query: str | None = None,
        *,
        limit: int | None = None,
        offset: int | None = None,
        dismax: bool = False,
        **fields: object,
    ) -> models.Page[T]:
        """Search an entity index.

        See
        [`EntityResource.search`][musicbrainz.client.EntityResource.search].
        """
        return await self._session.run(
            self._core.search(self.entity, query, fields, limit, offset, dismax)
        )

    def iter_search(
        self,
        query: str | None = None,
        *,
        page_size: int = consts.MAX_LIMIT,
        max_items: int | None = None,
        dismax: bool = False,
        **fields: object,
    ) -> AsyncIterator[T]:
        """Iterate over search results. Use ``max_items`` to stop early."""
        return self._session.paginate(
            lambda off: self._core.search(
                self.entity, query, fields, page_size, off, dismax
            ),
            max_items,
        )


class AsyncSearchOnlyResource[T]:
    """Entities that can only be searched (annotation, cdstub, tag)."""

    def __init__(self, session: _AsyncSession, entity: str) -> None:
        self._session = session
        self._core = session.core
        self.entity = entity

    search = AsyncEntityResource.search
    iter_search = AsyncEntityResource.iter_search


class AsyncUrlResource(AsyncEntityResource[models.Url]):
    async def by_resource(self, url: str, *, inc: Includes = None) -> models.Url:
        """Look up a URL entity by its text.

        Raises:
            NotFoundError: If MusicBrainz doesn't know the URL.
        """
        return (await self._session.run(self._core.lookup_urls([url], inc)))[0]

    async def by_resources(
        self, urls: Iterable[str], *, inc: Includes = None
    ) -> list[models.Url]:
        """Look up many URLs (batched by 100). Unknown URLs are silently skipped."""
        result: list[models.Url] = []
        for chunk in _chunks(list(urls), consts.URL_BATCH_SIZE):
            try:
                result += await self._session.run(self._core.lookup_urls(chunk, inc))
            except NotFoundError:
                if len(chunk) > 1:
                    raise
        return result


class AsyncGenreResource(AsyncEntityResource[models.Genre]):
    async def all(
        self, *, limit: int | None = None, offset: int | None = None
    ) -> models.Page[models.Genre]:
        """One page of the alphabetical list of all genres."""
        return await self._session.run(self._core.genres_all(limit, offset))

    def iter_all(
        self, *, page_size: int = consts.MAX_LIMIT, max_items: int | None = None
    ) -> AsyncIterator[models.Genre]:
        """Iterate over all genres."""
        return self._session.paginate(
            lambda off: self._core.genres_all(page_size, off), max_items
        )

    async def names(self) -> list[str]:
        """All genre names (single plain-text request)."""
        return await self._session.run(self._core.genre_names())


class AsyncCollectionResource(AsyncEntityResource[models.Collection]):
    async def of_editor(
        self,
        editor: str,
        *,
        include_private: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> models.Page[models.Collection]:
        """Collections of an editor.

        ``include_private`` requires authenticating as that editor.
        """
        return await self.browse(
            editor=editor,
            inc=["user-collections"] if include_private else None,
            limit=limit,
            offset=offset,
        )

    async def summary(self, mbid: str, entity_type: str) -> models.Collection:
        """Collection description with a summary of its ``entity_type`` contents."""
        return await self._session.run(
            self._core.lookup_collection_contents(mbid, entity_type)
        )

    async def items(
        self, mbid: str, entity_type: str, **options: Unpack[BrowseOptions]
    ) -> models.Page[Any]:
        """One page of collection contents, e.g. ``items(mbid, "release")``."""
        return await self._session.resource(entity_type).browse(
            collection=mbid, **options
        )

    def iter_items(
        self, mbid: str, entity_type: str, **options: Unpack[IterBrowseOptions]
    ) -> AsyncIterator[Any]:
        """Iterate over all collection contents."""
        return self._session.resource(entity_type).iter_browse(
            collection=mbid, **options
        )

    async def add(self, mbid: str, entity_type: str, mbids: Iterable[str]) -> None:
        """Add entities to a collection you own (batched automatically)."""
        for chunk in _chunks(list(mbids), consts.COLLECTION_BATCH_SIZE):
            await self._session.run(
                self._core.collection_modify("PUT", mbid, entity_type, chunk)
            )

    async def remove(self, mbid: str, entity_type: str, mbids: Iterable[str]) -> None:
        """Remove entities from a collection you own (batched automatically)."""
        for chunk in _chunks(list(mbids), consts.COLLECTION_BATCH_SIZE):
            await self._session.run(
                self._core.collection_modify("DELETE", mbid, entity_type, chunk)
            )


class AsyncMusicBrainz:
    """Asynchronous MusicBrainz WS/2 client (asyncio or trio).

    Same constructor arguments and API as
    [`MusicBrainz`][musicbrainz.client.MusicBrainz]; every request method is
    awaited and iterators are async iterators.

    Example:
        ```python
        async with AsyncMusicBrainz("MyApp", "1.0", "me@example.com") as mb:
            page = await mb.artists.search("Nirvana")
            async for rg in mb.release_groups.iter_browse(artist=page[0].id):
                print(rg.title)
        ```
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
        base_url: str = consts.DEFAULT_BASE_URL,
        rate_limit: float | None = 1.0,
        rate_limiter: AsyncRateLimiter | None = None,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        timeout: float = 30.0,
        validate: bool = True,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        config = _Config(
            app,
            version,
            contact,
            username=username,
            password=password,
            oauth_token=oauth_token,
            auth=auth,
            base_url=base_url,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            validate=validate,
        )
        http = httpx2.AsyncClient(
            base_url=config.base_url,
            headers=config.headers,
            auth=config.auth,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )
        limiter = rate_limiter or (AsyncRateLimiter(rate_limit) if rate_limit else None)
        self._session = s = _AsyncSession(config, limiter, http)
        self._core = config.core
        self.user_agent = config.user_agent

        self.areas = AsyncEntityResource[models.Area](s, "area")
        self.artists = AsyncEntityResource[models.Artist](s, "artist")
        self.events = AsyncEntityResource[models.Event](s, "event")
        self.genres = AsyncGenreResource(s, "genre")
        self.instruments = AsyncEntityResource[models.Instrument](s, "instrument")
        self.labels = AsyncEntityResource[models.Label](s, "label")
        self.places = AsyncEntityResource[models.Place](s, "place")
        self.recordings = AsyncEntityResource[models.Recording](s, "recording")
        self.releases = AsyncEntityResource[models.Release](s, "release")
        self.release_groups = AsyncEntityResource[models.ReleaseGroup](
            s, "release-group"
        )
        self.series = AsyncEntityResource[models.Series](s, "series")
        self.works = AsyncEntityResource[models.Work](s, "work")
        self.urls = AsyncUrlResource(s, "url")
        self.collections = AsyncCollectionResource(s, "collection")
        self.annotations = AsyncSearchOnlyResource[models.Annotation](s, "annotation")
        self.cdstubs = AsyncSearchOnlyResource[models.CDStub](s, "cdstub")
        self.tags = AsyncSearchOnlyResource[models.Tag](s, "tag")

    async def aclose(self) -> None:
        await self._session.http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def resource(self, entity: str) -> AsyncEntityResource[Any]:
        """The resource for an entity name, e.g. ``"release-group"``."""
        return self._session.resource(entity)

    async def lookup_discid(
        self,
        discid: str,
        *,
        inc: Includes = None,
        toc: str | Iterable[int] | None = None,
        cdstubs: bool = True,
        all_media_formats: bool = False,
        release_type: Filter = None,
        release_status: Filter = None,
    ) -> models.Disc | models.CDStub:
        """Releases for a disc ID.

        See
        [`MusicBrainz.lookup_discid`][musicbrainz.client.MusicBrainz.lookup_discid].
        """
        return await self._session.run(
            self._core.lookup_discid(
                discid,
                inc,
                toc=toc,
                cdstubs=cdstubs,
                all_media_formats=all_media_formats,
                release_type=release_type,
                release_status=release_status,
            )
        )

    async def lookup_isrc(
        self, isrc: str, *, inc: Includes = None
    ) -> models.IsrcResult:
        """Recordings with the given ISRC."""
        return await self._session.run(self._core.lookup_isrc(isrc, inc))

    async def lookup_iswc(
        self, iswc: str, *, inc: Includes = None
    ) -> list[models.Work]:
        """Works with the given ISWC."""
        return await self._session.run(self._core.lookup_iswc(iswc, inc))

    async def submit_tags(
        self,
        entity: str,
        tags: Mapping[str, Iterable[str]],
        *,
        vote: Vote | None = None,
    ) -> None:
        """Tag entities.

        See [`MusicBrainz.submit_tags`][musicbrainz.client.MusicBrainz.submit_tags].
        """
        await self._session.run(self._core.submit_tags(entity, tags, vote))

    async def submit_ratings(self, entity: str, ratings: Mapping[str, int]) -> None:
        """Rate entities 0-100 (20 per star; 0 removes your rating)."""
        await self._session.run(self._core.submit_ratings(entity, ratings))

    async def submit_barcodes(self, barcodes: Mapping[str, str]) -> None:
        """Add barcodes to releases (``{release_mbid: "GTIN"}``). Creates edits."""
        await self._session.run(self._core.submit_barcodes(barcodes))

    async def submit_isrcs(self, isrcs: Mapping[str, Iterable[str]]) -> None:
        """Add ISRCs to recordings (``{recording_mbid: ["USxxx..."]}``)."""
        await self._session.run(self._core.submit_isrcs(isrcs))

    async def request(
        self, method: str, path: str, params: Mapping[str, str] | None = None
    ) -> JSON:
        """Escape hatch: perform a raw JSON request and return the decoded body."""
        return await self._session.run(_raw_call(method, path, params))
