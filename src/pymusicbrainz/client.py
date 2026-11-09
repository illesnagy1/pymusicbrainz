"""Synchronous and asynchronous MusicBrainz clients."""

from __future__ import annotations

import logging
import time
from collections.abc import (
    AsyncIterator,
    Callable,
    Generator,
    Iterable,
    Iterator,
    Mapping,
)
from typing import Any, Literal, Self, Unpack

import anyio
import httpx2
from typing_extensions import TypedDict  # closed=True (PEP 728) before 3.15

from . import _constants as consts
from . import models
from ._core import (
    JSON,
    Call,
    Core,
    CoverArtSize,
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
        caa_url: str,
        max_retries: int,
        backoff_factor: float,
        validate: bool,
    ) -> None:
        if not app or not version:
            msg = "app and version are required to build a meaningful User-Agent"
            raise ValidationError(msg)
        self.user_agent = (
            f"{app}/{version}"
            + (f" ( {contact} )" if contact else "")
            + f" pymusicbrainz/{__version__}"
        )
        if sum(x is not None for x in (password, oauth_token, auth)) > 1:
            msg = "pass only one of username/password, oauth_token or auth"
            raise ValidationError(msg)
        if password is not None:
            if not username:
                msg = "username is required with password"
                raise ValidationError(msg)
            auth = httpx2.DigestAuth(username, password)
        if oauth_token is not None:
            auth = _BearerAuth(oauth_token)
        # Auth is applied per request (MusicBrainz only), never as a default
        # header, so credentials can't leak to the Cover Art Archive/archive.org.
        self.auth = auth
        self.headers = {"User-Agent": self.user_agent}
        self.has_auth = auth is not None
        # The API wants "<app>-<version>" where version contains no hyphen.
        client_id = f"{app}-{version}" if "-" not in version else None
        self.core = Core(
            client_id=client_id,
            has_auth=self.has_auth,
            validate=validate,
            caa_url=caa_url,
        )
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def check_auth(self, call: Call[Any]) -> None:
        if call.requires_auth and not self.has_auth:
            msg = f"{call.method} {call.path} requires authentication"
            raise AuthenticationRequiredError(msg)


class _BearerAuth(httpx2.Auth):
    """OAuth2 bearer token authentication."""

    def __init__(self, token: str) -> None:
        self._header = f"Bearer {token}"

    def auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, httpx2.Response]:
        request.headers["Authorization"] = self._header
        yield request


def _http_client[C: (httpx2.Client, httpx2.AsyncClient)](factory: Callable[[], C]) -> C:
    """Create the HTTP client, with a useful hint if SOCKS support is missing.

    Returns:
        The client ``factory`` created.

    Raises:
        ImportError: If a SOCKS proxy is set but ``socksio`` isn't installed,
            with an install hint; other import errors propagate unchanged.
    """
    try:
        return factory()
    except ImportError as exc:
        if "socksio" not in str(exc):
            raise
        msg = "SOCKS proxies need the socks extra: pip install 'pymusicbrainz[socks]'"
        raise ImportError(msg) from exc


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
    """Normalize ``"releases"`` etc. to the singular entity name.

    Returns:
        The singular entity name.
    """
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
            msg = f"unknown entity type: {entity!r}"
            raise ValidationError(msg) from None

    def run[R](self, call: Call[R]) -> R:
        cfg = self.config
        cfg.check_auth(call)
        for attempt in range(cfg.max_retries + 1):
            if self.limiter and not call.external:
                self.limiter.acquire()
            started = time.perf_counter()
            try:
                resp = self.http.request(
                    call.method,
                    call.path,
                    params=call.request_params(),
                    content=call.content,
                    headers=call.request_headers(),
                    auth=None if call.external else httpx2.USE_CLIENT_DEFAULT,
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
            content_type = resp.headers.get("Content-Type", "")
            body = decode_body(call, resp.content, content_type, str(resp.url))
            return call.parse(body)
        raise AssertionError  # unreachable: every attempt returns or raises

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
        """Created by the client; not meant to be instantiated directly."""
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

        Returns:
            The entity, e.g. an [`Artist`][pymusicbrainz.models.Artist] for
            ``mb.artists``.
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

        Returns:
            One page of linked entities.
        """
        return self._session.run(
            self._core.browse(
                self.entity,
                linked,
                inc,
                limit,
                offset,
                release_type=release_type,
                release_status=release_status,
                release_group_status=release_group_status,
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
        """Iterate over all browse results, fetching pages as needed.

        Returns:
            An iterator over every linked entity.
        """
        return self._session.paginate(
            lambda off: self._core.browse(
                self.entity,
                linked,
                inc,
                page_size,
                off,
                release_type=release_type,
                release_status=release_status,
                release_group_status=release_group_status,
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

        Returns:
            One page of results, best match first; each has a ``score``.
        """
        return self._session.run(
            self._core.search(self.entity, query, fields, limit, offset, dismax=dismax)
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
        """Iterate over search results. Use ``max_items`` to stop early.

        Returns:
            An iterator over every result.
        """
        return self._session.paginate(
            lambda off: self._core.search(
                self.entity, query, fields, page_size, off, dismax=dismax
            ),
            max_items,
        )


class SearchOnlyResource[T]:
    """Entities that can only be searched (annotation, cdstub, tag)."""

    def __init__(self, session: _Session, entity: str) -> None:
        """Created by the client; not meant to be instantiated directly."""
        self._session = session
        self._core = session.core
        self.entity = entity

    search = EntityResource.search
    iter_search = EntityResource.iter_search


class UrlResource(EntityResource[models.Url]):
    """``mb.urls``: URL entities, also looked up by the URL text itself."""

    def by_resource(self, url: str, *, inc: Includes = None) -> models.Url:
        """Look up a URL entity by its text.

        A URL MusicBrainz doesn't know raises
        [`NotFoundError`][pymusicbrainz.errors.NotFoundError].

        Returns:
            The URL entity.
        """
        return self._session.run(self._core.lookup_urls([url], inc))[0]

    def by_resources(
        self, urls: Iterable[str], *, inc: Includes = None
    ) -> list[models.Url]:
        """Look up many URLs (batched by 100). Unknown URLs are silently skipped.

        Returns:
            The URL entities MusicBrainz knows, in no particular order.

        Raises:
            NotFoundError: If MusicBrainz rejects a whole batch of several URLs.
        """
        result: list[models.Url] = []
        for chunk in _chunks(list(urls), consts.URL_BATCH_SIZE):
            try:
                result += self._session.run(self._core.lookup_urls(chunk, inc))
            except NotFoundError:
                if len(chunk) > 1:
                    raise
        return result


class GenreResource(EntityResource[models.Genre]):
    """``mb.genres``: genres, plus the full alphabetical genre list."""

    def all(
        self, *, limit: int | None = None, offset: int | None = None
    ) -> models.Page[models.Genre]:
        """One page of the alphabetical list of all genres.

        Returns:
            One page of genres, alphabetically.
        """
        return self._session.run(self._core.genres_all(limit, offset))

    def iter_all(
        self, *, page_size: int = consts.MAX_LIMIT, max_items: int | None = None
    ) -> Iterator[models.Genre]:
        """Iterate over all genres.

        Returns:
            An iterator over every genre.
        """
        return self._session.paginate(
            lambda off: self._core.genres_all(page_size, off), max_items
        )

    def names(self) -> list[str]:
        """All genre names (single plain-text request).

        Returns:
            Every genre name, alphabetically.
        """
        return self._session.run(self._core.genre_names())


class CollectionResource(EntityResource[models.Collection]):
    """``mb.collections``: read collections and change your own."""

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

        Returns:
            One page of the editor's collections.
        """
        return self.browse(
            editor=editor,
            inc=["user-collections"] if include_private else None,
            limit=limit,
            offset=offset,
        )

    def summary(self, mbid: str, entity_type: str) -> models.Collection:
        """Collection description with a summary of its ``entity_type`` contents.

        Returns:
            The collection, with its item count.
        """
        return self._session.run(
            self._core.lookup_collection_contents(mbid, entity_type)
        )

    def items(
        self, mbid: str, entity_type: str, **options: Unpack[BrowseOptions]
    ) -> models.Page[Any]:
        """One page of collection contents, e.g. ``items(mbid, "release")``.

        Returns:
            One page of the entities in the collection.
        """
        return self._session.resource(entity_type).browse(collection=mbid, **options)

    def iter_items(
        self, mbid: str, entity_type: str, **options: Unpack[IterBrowseOptions]
    ) -> Iterator[Any]:
        """Iterate over all collection contents.

        Returns:
            An iterator over every entity in the collection.
        """
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


class CoverArtResource:
    """``mb.cover_art``: images from the Cover Art Archive.

    The Cover Art Archive has no rate limit of its own, so these requests don't
    wait for the MusicBrainz rate limiter; credentials are never sent to it.
    """

    def __init__(self, session: _Session) -> None:
        """Created by the client; not meant to be instantiated directly."""
        self._session = session
        self._core = session.core

    def release(self, mbid: str) -> models.CoverArt:
        """List a release's images.

        A release without cover art (or an unknown one) raises
        [`NotFoundError`][pymusicbrainz.errors.NotFoundError].

        Returns:
            The release's images.
        """
        return self._session.run(self._core.cover_art_list(mbid))

    def release_group(self, mbid: str) -> models.CoverArt:
        """List the images of the release chosen to represent a release group.

        A release group without cover art raises
        [`NotFoundError`][pymusicbrainz.errors.NotFoundError].

        Returns:
            The images of the release chosen to represent the group.
        """
        return self._session.run(self._core.cover_art_list(mbid, release_group=True))

    def image(
        self,
        mbid: str,
        image: str | int = "front",
        *,
        size: CoverArtSize | None = None,
        release_group: bool = False,
    ) -> models.ImageData:
        """Download an image.

        A missing image or thumbnail raises
        [`NotFoundError`][pymusicbrainz.errors.NotFoundError].

        Args:
            mbid: Release MBID (or release group MBID with ``release_group``).
            image: ``"front"``, ``"back"``, or an image ID from a listing.
                Release groups only have ``"front"``.
            size: A 250, 500 or 1200 px thumbnail instead of the original.
            release_group: ``mbid`` is a release group.

        Returns:
            The image's bytes, content type and final URL.
        """
        return self._session.run(
            self._core.cover_art_image(mbid, image, size, release_group=release_group)
        )

    def front(
        self,
        mbid: str,
        *,
        size: CoverArtSize | None = None,
        release_group: bool = False,
    ) -> models.ImageData:
        """Download the main front cover.

        See [`image`][pymusicbrainz.client.CoverArtResource.image].

        Returns:
            The image's bytes, content type and final URL.
        """
        return self.image(mbid, "front", size=size, release_group=release_group)

    def back(self, mbid: str, *, size: CoverArtSize | None = None) -> models.ImageData:
        """Download a release's main back cover.

        Returns:
            The image's bytes, content type and final URL.
        """
        return self.image(mbid, "back", size=size)

    def url(
        self,
        mbid: str,
        image: str | int = "front",
        *,
        size: CoverArtSize | None = None,
        release_group: bool = False,
    ) -> str:
        """The URL of an image, without downloading it (e.g. for an ``<img>``).

        Returns:
            The image URL.
        """
        return self._core.cover_art_url(mbid, image, size, release_group=release_group)


class MusicBrainz:
    """Synchronous MusicBrainz WS/2 client.

    Entity namespaces (`areas`, `artists`, `release_groups`, ...) are
    [`EntityResource`][pymusicbrainz.client.EntityResource] instances; see the
    [API overview](../guide/lookups.md) for what they can do.

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
        caa_url: str = consts.DEFAULT_CAA_URL,
        rate_limit: float | None = 1.0,
        rate_limiter: RateLimiter | None = None,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        timeout: float = 30.0,
        proxy: str | httpx2.Proxy | None = None,
        validate: bool = True,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        """Create a client.

        Empty ``app``/``version`` or conflicting auth options raise
        [`ValidationError`][pymusicbrainz.errors.ValidationError].

        Args:
            app: Your application's name, used in the User-Agent and as the
                submission client ID.
            version: Your application's version. It must not contain a hyphen
                if you want to submit data.
            contact: URL or e-mail address where MusicBrainz admins can reach
                you.
            username: Account name for digest authentication.
            password: A token from your MusicBrainz *Applications* page (not
                your account password), used with `username`.
            oauth_token: OAuth2 bearer token, an alternative to digest auth.
            auth: Any other `httpx2.Auth` implementation.
            base_url: API root, e.g. a private mirror.
            caa_url: Cover Art Archive root.
            rate_limit: Maximum requests per second; `None` disables limiting.
            rate_limiter: A [`RateLimiter`][pymusicbrainz.RateLimiter] shared with other
                clients; overrides `rate_limit`.
            max_retries: Retries on 429/502/503/504 responses and network
                errors.
            backoff_factor: Base of the exponential back-off, in seconds.
            timeout: Per-request timeout in seconds.
            proxy: Proxy URL for all requests, e.g. ``"http://proxy:3128"`` or
                ``"socks5://proxy:1080"`` (SOCKS needs the ``socks`` extra).
                Without it, the ``HTTP_PROXY``/``HTTPS_PROXY``/``ALL_PROXY``
                and ``NO_PROXY`` environment variables are used.
            validate: Check includes, filters and search fields client-side
                before sending.
            transport: Custom `httpx2` transport, e.g. `httpx2.MockTransport`
                in tests.

        """
        config = _Config(
            app,
            version,
            contact,
            username=username,
            password=password,
            oauth_token=oauth_token,
            auth=auth,
            base_url=base_url,
            caa_url=caa_url,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            validate=validate,
        )
        http = _http_client(
            lambda: httpx2.Client(
                base_url=config.base_url,
                headers=config.headers,
                auth=config.auth,
                timeout=timeout,
                proxy=proxy,
                transport=transport,
                follow_redirects=True,
            )
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
        self.cover_art = CoverArtResource(s)
        self.annotations = SearchOnlyResource[models.Annotation](s, "annotation")
        self.cdstubs = SearchOnlyResource[models.CDStub](s, "cdstub")
        self.tags = SearchOnlyResource[models.Tag](s, "tag")

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        """Close the HTTP connections; the client can't be used afterwards."""
        self._session.http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def resource(self, entity: str) -> EntityResource[Any]:
        """The resource for an entity name, e.g. ``"release-group"``.

        Returns:
            The namespace, e.g. ``mb.release_groups`` for ``"release-group"``.
        """
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
        Returns a [`Disc`][pymusicbrainz.models.Disc] (with ``releases``) or,
        if only a CD stub matches, a [`CDStub`][pymusicbrainz.models.CDStub].

        Returns:
            The disc with its releases, or a CD stub.
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
        """Recordings with the given ISRC.

        Returns:
            The ISRC with its recordings.
        """
        return self._session.run(self._core.lookup_isrc(isrc, inc))

    def lookup_iswc(self, iswc: str, *, inc: Includes = None) -> list[models.Work]:
        """Works with the given ISWC.

        Returns:
            The works with this ISWC.
        """
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
        """Escape hatch: perform a raw JSON request and return the decoded body.

        Returns:
            The decoded JSON response.
        """
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
            msg = f"unknown entity type: {entity!r}"
            raise ValidationError(msg) from None

    async def run[R](self, call: Call[R]) -> R:
        cfg = self.config
        cfg.check_auth(call)
        for attempt in range(cfg.max_retries + 1):
            if self.limiter and not call.external:
                await self.limiter.acquire()
            started = time.perf_counter()
            try:
                resp = await self.http.request(
                    call.method,
                    call.path,
                    params=call.request_params(),
                    content=call.content,
                    headers=call.request_headers(),
                    auth=None if call.external else httpx2.USE_CLIENT_DEFAULT,
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
            content_type = resp.headers.get("Content-Type", "")
            body = decode_body(call, resp.content, content_type, str(resp.url))
            return call.parse(body)
        raise AssertionError  # unreachable: every attempt returns or raises

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
    """Async counterpart of [`EntityResource`][pymusicbrainz.client.EntityResource]."""

    def __init__(self, session: _AsyncSession, entity: str) -> None:
        """Created by the client; not meant to be instantiated directly."""
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

        See [`EntityResource.get`][pymusicbrainz.client.EntityResource.get].

        Returns:
            The entity, e.g. an [`Artist`][pymusicbrainz.models.Artist] for
            ``mb.artists``.
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
        [`EntityResource.browse`][pymusicbrainz.client.EntityResource.browse].

        Returns:
            One page of linked entities.
        """
        return await self._session.run(
            self._core.browse(
                self.entity,
                linked,
                inc,
                limit,
                offset,
                release_type=release_type,
                release_status=release_status,
                release_group_status=release_group_status,
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
        """Iterate over all browse results, fetching pages as needed.

        Returns:
            An iterator over every linked entity.
        """
        return self._session.paginate(
            lambda off: self._core.browse(
                self.entity,
                linked,
                inc,
                page_size,
                off,
                release_type=release_type,
                release_status=release_status,
                release_group_status=release_group_status,
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
        [`EntityResource.search`][pymusicbrainz.client.EntityResource.search].

        Returns:
            One page of results, best match first; each has a ``score``.
        """
        return await self._session.run(
            self._core.search(self.entity, query, fields, limit, offset, dismax=dismax)
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
        """Iterate over search results. Use ``max_items`` to stop early.

        Returns:
            An iterator over every result.
        """
        return self._session.paginate(
            lambda off: self._core.search(
                self.entity, query, fields, page_size, off, dismax=dismax
            ),
            max_items,
        )


class AsyncSearchOnlyResource[T]:
    """Entities that can only be searched (annotation, cdstub, tag)."""

    def __init__(self, session: _AsyncSession, entity: str) -> None:
        """Created by the client; not meant to be instantiated directly."""
        self._session = session
        self._core = session.core
        self.entity = entity

    search = AsyncEntityResource.search
    iter_search = AsyncEntityResource.iter_search


class AsyncUrlResource(AsyncEntityResource[models.Url]):
    """Async counterpart of [`UrlResource`][pymusicbrainz.client.UrlResource]."""

    async def by_resource(self, url: str, *, inc: Includes = None) -> models.Url:
        """Look up a URL entity by its text.

        A URL MusicBrainz doesn't know raises
        [`NotFoundError`][pymusicbrainz.errors.NotFoundError].

        Returns:
            The URL entity.
        """
        return (await self._session.run(self._core.lookup_urls([url], inc)))[0]

    async def by_resources(
        self, urls: Iterable[str], *, inc: Includes = None
    ) -> list[models.Url]:
        """Look up many URLs (batched by 100). Unknown URLs are silently skipped.

        Returns:
            The URL entities MusicBrainz knows, in no particular order.

        Raises:
            NotFoundError: If MusicBrainz rejects a whole batch of several URLs.
        """
        result: list[models.Url] = []
        for chunk in _chunks(list(urls), consts.URL_BATCH_SIZE):
            try:
                result += await self._session.run(self._core.lookup_urls(chunk, inc))
            except NotFoundError:
                if len(chunk) > 1:
                    raise
        return result


class AsyncGenreResource(AsyncEntityResource[models.Genre]):
    """Async genres namespace.

    See [`GenreResource`][pymusicbrainz.client.GenreResource].
    """

    async def all(
        self, *, limit: int | None = None, offset: int | None = None
    ) -> models.Page[models.Genre]:
        """One page of the alphabetical list of all genres.

        Returns:
            One page of genres, alphabetically.
        """
        return await self._session.run(self._core.genres_all(limit, offset))

    def iter_all(
        self, *, page_size: int = consts.MAX_LIMIT, max_items: int | None = None
    ) -> AsyncIterator[models.Genre]:
        """Iterate over all genres.

        Returns:
            An iterator over every genre.
        """
        return self._session.paginate(
            lambda off: self._core.genres_all(page_size, off), max_items
        )

    async def names(self) -> list[str]:
        """All genre names (single plain-text request).

        Returns:
            Every genre name, alphabetically.
        """
        return await self._session.run(self._core.genre_names())


class AsyncCollectionResource(AsyncEntityResource[models.Collection]):
    """Async collections namespace.

    See [`CollectionResource`][pymusicbrainz.client.CollectionResource].
    """

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

        Returns:
            One page of the editor's collections.
        """
        return await self.browse(
            editor=editor,
            inc=["user-collections"] if include_private else None,
            limit=limit,
            offset=offset,
        )

    async def summary(self, mbid: str, entity_type: str) -> models.Collection:
        """Collection description with a summary of its ``entity_type`` contents.

        Returns:
            The collection, with its item count.
        """
        return await self._session.run(
            self._core.lookup_collection_contents(mbid, entity_type)
        )

    async def items(
        self, mbid: str, entity_type: str, **options: Unpack[BrowseOptions]
    ) -> models.Page[Any]:
        """One page of collection contents, e.g. ``items(mbid, "release")``.

        Returns:
            One page of the entities in the collection.
        """
        return await self._session.resource(entity_type).browse(
            collection=mbid, **options
        )

    def iter_items(
        self, mbid: str, entity_type: str, **options: Unpack[IterBrowseOptions]
    ) -> AsyncIterator[Any]:
        """Iterate over all collection contents.

        Returns:
            An iterator over every entity in the collection.
        """
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


class AsyncCoverArtResource:
    """Async Cover Art Archive namespace.

    See [`CoverArtResource`][pymusicbrainz.client.CoverArtResource].
    """

    def __init__(self, session: _AsyncSession) -> None:
        """Created by the client; not meant to be instantiated directly."""
        self._session = session
        self._core = session.core

    async def release(self, mbid: str) -> models.CoverArt:
        """List a release's images.

        Returns:
            The release's images.
        """
        return await self._session.run(self._core.cover_art_list(mbid))

    async def release_group(self, mbid: str) -> models.CoverArt:
        """List the images of the release chosen to represent a release group.

        Returns:
            The images of the release chosen to represent the group.
        """
        return await self._session.run(
            self._core.cover_art_list(mbid, release_group=True)
        )

    async def image(
        self,
        mbid: str,
        image: str | int = "front",
        *,
        size: CoverArtSize | None = None,
        release_group: bool = False,
    ) -> models.ImageData:
        """Download an image.

        See [`CoverArtResource.image`][pymusicbrainz.client.CoverArtResource.image].

        Returns:
            The image's bytes, content type and final URL.
        """
        return await self._session.run(
            self._core.cover_art_image(mbid, image, size, release_group=release_group)
        )

    async def front(
        self,
        mbid: str,
        *,
        size: CoverArtSize | None = None,
        release_group: bool = False,
    ) -> models.ImageData:
        """Download the main front cover.

        Returns:
            The image's bytes, content type and final URL.
        """
        return await self.image(mbid, "front", size=size, release_group=release_group)

    async def back(
        self, mbid: str, *, size: CoverArtSize | None = None
    ) -> models.ImageData:
        """Download a release's main back cover.

        Returns:
            The image's bytes, content type and final URL.
        """
        return await self.image(mbid, "back", size=size)

    def url(
        self,
        mbid: str,
        image: str | int = "front",
        *,
        size: CoverArtSize | None = None,
        release_group: bool = False,
    ) -> str:
        """The URL of an image, without downloading it.

        Returns:
            The image URL.
        """
        return self._core.cover_art_url(mbid, image, size, release_group=release_group)


class AsyncMusicBrainz:
    """Asynchronous MusicBrainz WS/2 client (asyncio or trio).

    The same API as [`MusicBrainz`][pymusicbrainz.client.MusicBrainz]: every
    request method is awaited, and iterators are async iterators.

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
        caa_url: str = consts.DEFAULT_CAA_URL,
        rate_limit: float | None = 1.0,
        rate_limiter: AsyncRateLimiter | None = None,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        timeout: float = 30.0,
        proxy: str | httpx2.Proxy | None = None,
        validate: bool = True,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Create an async client.

        Empty ``app``/``version`` or conflicting auth options raise
        [`ValidationError`][pymusicbrainz.errors.ValidationError].

        Args:
            app: Your application's name, used in the User-Agent and as the
                submission client ID.
            version: Your application's version. It must not contain a hyphen
                if you want to submit data.
            contact: URL or e-mail address where MusicBrainz admins can reach
                you.
            username: Account name for digest authentication.
            password: A token from your MusicBrainz *Applications* page (not
                your account password), used with `username`.
            oauth_token: OAuth2 bearer token, an alternative to digest auth.
            auth: Any other `httpx2.Auth` implementation.
            base_url: API root, e.g. a private mirror.
            caa_url: Cover Art Archive root.
            rate_limit: Maximum requests per second; `None` disables limiting.
            rate_limiter: An
                [`AsyncRateLimiter`][pymusicbrainz.AsyncRateLimiter] shared with
                other clients; overrides `rate_limit`.
            max_retries: Retries on 429/502/503/504 responses and network
                errors.
            backoff_factor: Base of the exponential back-off, in seconds.
            timeout: Per-request timeout in seconds.
            proxy: Proxy URL for all requests, e.g. ``"http://proxy:3128"`` or
                ``"socks5://proxy:1080"`` (SOCKS needs the ``socks`` extra).
                Without it, the ``HTTP_PROXY``/``HTTPS_PROXY``/``ALL_PROXY``
                and ``NO_PROXY`` environment variables are used.
            validate: Check includes, filters and search fields client-side
                before sending.
            transport: Custom async `httpx2` transport, e.g.
                `httpx2.MockTransport` in tests.

        """
        config = _Config(
            app,
            version,
            contact,
            username=username,
            password=password,
            oauth_token=oauth_token,
            auth=auth,
            base_url=base_url,
            caa_url=caa_url,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            validate=validate,
        )
        http = _http_client(
            lambda: httpx2.AsyncClient(
                base_url=config.base_url,
                headers=config.headers,
                auth=config.auth,
                timeout=timeout,
                proxy=proxy,
                transport=transport,
                follow_redirects=True,
            )
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
        self.cover_art = AsyncCoverArtResource(s)
        self.annotations = AsyncSearchOnlyResource[models.Annotation](s, "annotation")
        self.cdstubs = AsyncSearchOnlyResource[models.CDStub](s, "cdstub")
        self.tags = AsyncSearchOnlyResource[models.Tag](s, "tag")

    async def aclose(self) -> None:
        """Close the HTTP connections; the client can't be used afterwards."""
        await self._session.http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def resource(self, entity: str) -> AsyncEntityResource[Any]:
        """The resource for an entity name, e.g. ``"release-group"``.

        Returns:
            The namespace, e.g. ``mb.release_groups`` for ``"release-group"``.
        """
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
        [`MusicBrainz.lookup_discid`][pymusicbrainz.client.MusicBrainz.lookup_discid].

        Returns:
            The disc with its releases, or a CD stub.
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
        """Recordings with the given ISRC.

        Returns:
            The ISRC with its recordings.
        """
        return await self._session.run(self._core.lookup_isrc(isrc, inc))

    async def lookup_iswc(
        self, iswc: str, *, inc: Includes = None
    ) -> list[models.Work]:
        """Works with the given ISWC.

        Returns:
            The works with this ISWC.
        """
        return await self._session.run(self._core.lookup_iswc(iswc, inc))

    async def submit_tags(
        self,
        entity: str,
        tags: Mapping[str, Iterable[str]],
        *,
        vote: Vote | None = None,
    ) -> None:
        """Tag entities.

        See [`MusicBrainz.submit_tags`][pymusicbrainz.client.MusicBrainz.submit_tags].
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
        """Escape hatch: perform a raw JSON request and return the decoded body.

        Returns:
            The decoded JSON response.
        """
        return await self._session.run(_raw_call(method, path, params))
