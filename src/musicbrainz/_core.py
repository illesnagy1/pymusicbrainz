"""Sans-IO core: builds request descriptions and parses responses.

No networking happens here; the clients in `musicbrainz.client` execute the
`Call` objects built by `Core`.
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from httpx2 import codes

from . import _constants as consts
from . import models
from .errors import AuthenticationRequiredError, ValidationError, error_for_status
from .query import build_query

type JSON = dict[str, JSON] | list[JSON] | str | int | float | bool | None
Includes = str | Iterable[str] | None
Filter = str | Iterable[str] | None

_MBID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_ISRC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")
_ISWC_RE = re.compile(r"^T-?\d{3}\.?\d{3}\.?\d{3}-?\d$")
_DISCID_RE = re.compile(r"^[A-Za-z0-9._-]{28}$|^-$")
_LIST_SEP_RE = re.compile(r"[+\s,|]+")
_DTD_RE = re.compile(r"<!(DOCTYPE|ENTITY)", re.IGNORECASE)
_HTML_P_RE = re.compile(r"<p>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_IMAGE_ID_RE = re.compile(r"^\d+(\.\w+)?$")
CoverArtSize = Literal[250, 500, 1200]

RETRY_STATUSES = frozenset(
    {
        codes.TOO_MANY_REQUESTS,
        codes.BAD_GATEWAY,
        codes.SERVICE_UNAVAILABLE,
        codes.GATEWAY_TIMEOUT,
    }
)


@dataclass(frozen=True, slots=True)
class Call[R]:
    """One HTTP request, plus how to turn its response body into a result."""

    method: str
    path: str
    params: list[tuple[str, str]]
    parse: Callable[[Any], R]
    content: bytes | None = None
    accept: Literal["json", "xml", "text", "bytes"] = "json"
    requires_auth: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    #: Not a MusicBrainz WS/2 request (e.g. the Cover Art Archive): no ``fmt``
    #: parameter, no MusicBrainz rate limit, and credentials are never sent.
    external: bool = False

    def request_params(self) -> tuple[tuple[str, str], ...]:
        if self.external:
            return tuple(self.params)
        fmt = {"json": [("fmt", "json")], "text": [("fmt", "txt")]}
        return (*self.params, *fmt.get(self.accept, []))

    def request_headers(self) -> dict[str, str]:
        accept = {
            "json": "application/json",
            "text": "text/plain",
            "xml": "application/xml",
            "bytes": "image/*",
        }[self.accept]
        return {"Accept": accept, **self.headers}


# ------------------------------------------------------------------ validation


def _fail(msg: str) -> ValidationError:
    return ValidationError(msg)


def check_mbid(value: str, what: str = "MBID") -> str:
    if not isinstance(value, str) or not _MBID_RE.match(value):
        raise _fail(f"invalid {what}: {value!r}")
    return value.lower()


def normalize_isrc(value: str) -> str:
    isrc = value.replace("-", "").replace(" ", "").upper()
    if not _ISRC_RE.match(isrc):
        raise _fail(f"invalid ISRC: {value!r}")
    return isrc


def normalize_iswc(value: str) -> str:
    v = value.strip().upper()
    if not _ISWC_RE.match(v):
        raise _fail(f"invalid ISWC: {value!r}")
    digits = re.sub(r"\D", "", v)
    return f"T-{digits[0:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9]}"


def check_gtin(barcode: str) -> str:
    """Validate a GTIN-8/12/13/14 (EAN/UPC) checksum; the API rejects bad ones."""
    if not barcode.isdigit() or len(barcode) not in (8, 12, 13, 14):
        raise _fail(f"invalid barcode (must be 8/12/13/14 digits): {barcode!r}")
    digits = [int(d) for d in barcode]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    if (10 - total % 10) % 10 != check:
        raise _fail(f"invalid barcode checksum: {barcode!r}")
    return barcode


def _as_list(value: str | Iterable[str] | None) -> list[str]:
    """Split ``"a+b"``, ``"a b"``, ``"a,b"``, ``"a|b"`` or ``["a", "b,c"]``."""
    if value is None:
        return []
    items = [value] if isinstance(value, str) else [str(v) for v in value]
    return [p for item in items for p in _LIST_SEP_RE.split(item) if p]


@dataclass(slots=True)
class Core:
    """Request builder configured per client."""

    client_id: str | None = None
    has_auth: bool = False
    validate: bool = True
    caa_url: str = consts.DEFAULT_CAA_URL

    # ------------------------------------------------------------ shared pieces

    def _includes(
        self, inc: Includes, allowed: frozenset[str] | None, entity: str
    ) -> list[str]:
        values = _as_list(inc)
        if self.validate and allowed is not None:
            bad = [v for v in values if v not in allowed]
            if bad:
                raise _fail(
                    f"invalid include(s) for {entity}: {', '.join(bad)}. "
                    f"Allowed: {', '.join(sorted(allowed))}"
                )
        return values

    def _inc_params(self, values: list[str]) -> list[tuple[str, str]]:
        return [("inc", "+".join(values))] if values else []

    def _auth_needed(self, values: Iterable[str]) -> bool:
        return any(v in consts.AUTH_INCLUDES for v in values)

    def _filters(
        self,
        incs: list[str] | None,
        release_type: Filter,
        release_status: Filter,
        *,
        browse_entity: str | None = None,
    ) -> list[tuple[str, str]]:
        types = [t.lower() for t in _as_list(release_type)]
        statuses = [s.lower() for s in _as_list(release_status)]
        if self.validate:
            _check_filter_values(types, statuses)
            if incs is not None:
                _check_lookup_filters(incs, types, statuses)
            if browse_entity is not None:
                _check_browse_filters(browse_entity, types, statuses)
        params: list[tuple[str, str]] = []
        if types:
            params.append(("type", "|".join(types)))
        if statuses:
            params.append(("status", "|".join(statuses)))
        return params

    def _paging(self, limit: int | None, offset: int | None) -> list[tuple[str, str]]:
        params = []
        if limit is not None:
            if not 1 <= limit <= consts.MAX_LIMIT:
                raise _fail(f"limit must be between 1 and {consts.MAX_LIMIT}")
            params.append(("limit", str(limit)))
        if offset is not None:
            if offset < 0:
                raise _fail("offset must be >= 0")
            params.append(("offset", str(offset)))
        return params

    # ------------------------------------------------------------ lookups

    def lookup(
        self,
        entity: str,
        mbid: str,
        inc: Includes = None,
        release_type: Filter = None,
        release_status: Filter = None,
    ) -> Call[Any]:
        mbid = check_mbid(mbid)
        incs = self._includes(inc, consts.LOOKUP_INCLUDES.get(entity), entity)
        params = self._inc_params(incs) + self._filters(
            incs, release_type, release_status
        )
        model = models.ENTITY_MODELS[entity]
        return Call(
            "GET",
            f"{entity}/{mbid}",
            params,
            model.model_validate,
            requires_auth=self._auth_needed(incs),
        )

    def lookup_discid(
        self,
        discid: str,
        inc: Includes = None,
        *,
        toc: str | Iterable[int] | None = None,
        cdstubs: bool = True,
        all_media_formats: bool = False,
        release_type: Filter = None,
        release_status: Filter = None,
    ) -> Call[models.Disc | models.CDStub]:
        if self.validate and not _DISCID_RE.match(discid):
            raise _fail(f"invalid disc ID: {discid!r}")
        incs = self._includes(inc, consts.LOOKUP_INCLUDES["discid"], "discid")
        params = self._inc_params(incs) + self._filters(
            incs or None, release_type, release_status
        )
        if toc is not None:
            params.append(
                (
                    "toc",
                    toc if isinstance(toc, str) else "+".join(str(int(x)) for x in toc),
                )
            )
        if not cdstubs:
            params.append(("cdstubs", "no"))
        if all_media_formats:
            params.append(("media-format", "all"))

        def parse(data: dict[str, Any]) -> models.Disc | models.CDStub:
            if "releases" in data or "sectors" in data or "offsets" in data:
                return models.Disc.model_validate(data)
            return models.CDStub.model_validate(data)

        return Call(
            "GET",
            f"discid/{discid}",
            params,
            parse,
            requires_auth=self._auth_needed(incs),
        )

    def lookup_isrc(self, isrc: str, inc: Includes = None) -> Call[models.IsrcResult]:
        isrc = normalize_isrc(isrc)
        incs = self._includes(inc, consts.LOOKUP_INCLUDES["isrc"], "isrc")
        return Call(
            "GET",
            f"isrc/{isrc}",
            self._inc_params(incs),
            models.IsrcResult.model_validate,
            requires_auth=self._auth_needed(incs),
        )

    def lookup_iswc(self, iswc: str, inc: Includes = None) -> Call[list[models.Work]]:
        iswc = normalize_iswc(iswc)
        incs = self._includes(inc, consts.LOOKUP_INCLUDES["iswc"], "iswc")

        def parse(data: dict[str, Any]) -> list[models.Work]:
            return [models.Work.model_validate(w) for w in data.get("works", [])]

        return Call(
            "GET",
            f"iswc/{iswc}",
            self._inc_params(incs),
            parse,
            requires_auth=self._auth_needed(incs),
        )

    def lookup_urls(
        self, resources: list[str], inc: Includes = None
    ) -> Call[list[models.Url]]:
        if not resources:
            raise _fail("at least one URL is required")
        if len(resources) > consts.URL_BATCH_SIZE:
            raise _fail(f"at most {consts.URL_BATCH_SIZE} URLs per request")
        incs = self._includes(inc, consts.LOOKUP_INCLUDES["url"], "url")
        params = [("resource", r) for r in resources] + self._inc_params(incs)

        def parse(data: dict[str, Any]) -> list[models.Url]:
            if "urls" in data:
                return [models.Url.model_validate(u) for u in data["urls"]]
            return [models.Url.model_validate(data)]

        return Call("GET", "url", params, parse)

    def lookup_collection_contents(
        self, mbid: str, entity_type: str
    ) -> Call[models.Collection]:
        mbid = check_mbid(mbid, "collection MBID")
        plural = self._collection_plural(entity_type)
        return Call(
            "GET", f"collection/{mbid}/{plural}", [], models.Collection.model_validate
        )

    # ------------------------------------------------------------ browse

    def browse(
        self,
        entity: str,
        links: Mapping[str, str],
        inc: Includes = None,
        limit: int | None = None,
        offset: int | None = None,
        release_type: Filter = None,
        release_status: Filter = None,
        release_group_status: str | None = None,
    ) -> Call[models.Page[Any]]:
        allowed = consts.BROWSE_LINKS.get(entity)
        if allowed is None:
            raise _fail(f"{entity} cannot be browsed")
        given = {_link_name(k): v for k, v in links.items() if v is not None}
        if len(given) != 1:
            raise _fail(
                "browse needs exactly one linked entity, one of: "
                + ", ".join(sorted(allowed))
            )
        ((link, value),) = given.items()
        if link not in allowed:
            raise _fail(
                f"cannot browse {entity} by {link}. "
                f"Allowed: {', '.join(sorted(allowed))}"
            )
        if link not in ("editor", "resource"):
            value = check_mbid(value, f"{link} MBID")
        incs = self._includes(inc, consts.BROWSE_INCLUDES.get(entity), entity)
        params = [(link, value), *self._inc_params(incs), *self._paging(limit, offset)]
        params += self._filters(
            None, release_type, release_status, browse_entity=entity
        )
        if release_group_status is not None:
            allowed_rgs = consts.RELEASE_GROUP_STATUSES
            if self.validate and release_group_status not in allowed_rgs:
                raise _fail(
                    f"release_group_status must be one of {sorted(allowed_rgs)}"
                )
            params.append(("release-group-status", release_group_status))
        return Call(
            "GET",
            entity,
            params,
            _page_parser(entity),
            requires_auth=self._auth_needed(incs),
        )

    def genres_all(
        self, limit: int | None = None, offset: int | None = None
    ) -> Call[models.Page[models.Genre]]:
        return Call(
            "GET", "genre/all", self._paging(limit, offset), _page_parser("genre")
        )

    def genre_names(self) -> Call[list[str]]:
        return Call(
            "GET",
            "genre/all",
            [],
            lambda text: [line for line in text.splitlines() if line.strip()],
            accept="text",
        )

    # ------------------------------------------------------------ search

    def search(
        self,
        entity: str,
        query: str | None = None,
        fields: Mapping[str, object] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        dismax: bool = False,
    ) -> Call[models.Page[Any]]:
        if entity not in consts.SEARCH_ENTITIES:
            raise _fail(
                f"{entity} is not searchable. "
                f"Searchable: {', '.join(consts.SEARCH_ENTITIES)}"
            )
        fields = dict(fields or {})
        if self.validate and (
            bad := sorted(set(fields) - consts.SEARCH_FIELDS[entity])
        ):
            raise _fail(
                f"invalid search field(s) for {entity}: {', '.join(bad)}. "
                f"Allowed: {', '.join(sorted(consts.SEARCH_FIELDS[entity]))}"
            )
        q = build_query(query, fields)
        params = [("query", q), *self._paging(limit, offset)]
        if dismax:
            params.append(("dismax", "true"))
        return Call("GET", entity, params, _page_parser(entity))

    # ------------------------------------------------------------ cover art

    def _caa_base(self, mbid: str, release_group: bool) -> str:
        kind = "release-group" if release_group else "release"
        base = self.caa_url if self.caa_url.endswith("/") else self.caa_url + "/"
        return f"{base}{kind}/{check_mbid(mbid)}"

    def cover_art_list(
        self, mbid: str, *, release_group: bool = False
    ) -> Call[models.CoverArt]:
        """Listing of the cover art of a release (or release group)."""
        return Call(
            "GET",
            self._caa_base(mbid, release_group),
            [],
            models.CoverArt.model_validate,
            external=True,
        )

    def cover_art_url(
        self,
        mbid: str,
        image: str | int = "front",
        size: CoverArtSize | None = None,
        *,
        release_group: bool = False,
    ) -> str:
        """URL of a cover art image (or thumbnail); no request is made."""
        name = str(image)
        if release_group and name != "front":
            raise _fail("release groups only have a 'front' image")
        if name not in ("front", "back") and not _IMAGE_ID_RE.match(name):
            raise _fail(f"image must be 'front', 'back' or an image ID: {image!r}")
        if size is not None:
            if size not in consts.COVER_ART_SIZES:
                raise _fail(f"size must be one of {consts.COVER_ART_SIZES}: {size!r}")
            stem, dot, ext = name.partition(".")
            name = f"{stem}-{size}{dot}{ext}"
        return f"{self._caa_base(mbid, release_group)}/{name}"

    def cover_art_image(
        self,
        mbid: str,
        image: str | int = "front",
        size: CoverArtSize | None = None,
        *,
        release_group: bool = False,
    ) -> Call[models.ImageData]:
        """Download a cover art image (or thumbnail)."""
        url = self.cover_art_url(mbid, image, size, release_group=release_group)
        return Call("GET", url, [], _image_data, accept="bytes", external=True)

    # ------------------------------------------------------------ collections

    def _collection_plural(self, entity_type: str) -> str:
        if entity_type in consts.COLLECTION_ENTITY_TYPES.values():
            return entity_type
        try:
            return consts.COLLECTION_ENTITY_TYPES[entity_type]
        except KeyError:
            raise _fail(f"invalid collection entity type: {entity_type!r}") from None

    def collection_modify(
        self,
        method: Literal["PUT", "DELETE"],
        collection: str,
        entity_type: str,
        mbids: list[str],
    ) -> Call[None]:
        collection = check_mbid(collection, "collection MBID")
        plural = self._collection_plural(entity_type)
        if not mbids:
            raise _fail("no MBIDs given")
        if len(mbids) > consts.COLLECTION_BATCH_SIZE:
            raise _fail(f"at most {consts.COLLECTION_BATCH_SIZE} entities per request")
        ids = ";".join(check_mbid(m) for m in mbids)
        return Call(
            method,
            f"collection/{collection}/{plural}/{ids}",
            self._client_param(),
            _ignore,
            accept="xml",
            requires_auth=True,
        )

    # ------------------------------------------------------------ submissions

    def _client_param(self) -> list[tuple[str, str]]:
        if not self.client_id:
            raise _fail(
                "submissions need a client ID: pass app and a version without '-'"
            )
        return [("client", self.client_id)]

    def _submission(self, path: str, root: ET.Element) -> Call[None]:
        if not self.has_auth:
            raise AuthenticationRequiredError("submissions require authentication")
        body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        return Call(
            "POST",
            path,
            self._client_param(),
            _ignore,
            content=body,
            accept="xml",
            requires_auth=True,
            headers={"Content-Type": "application/xml; charset=utf-8"},
        )

    def submit_tags(
        self,
        entity: str,
        tags: Mapping[str, Iterable[str]],
        vote: Literal["upvote", "downvote", "withdraw"] | None = None,
    ) -> Call[None]:
        if entity not in consts.TAGGABLE_ENTITIES:
            raise _fail(f"cannot tag {entity}")
        if vote not in (None, "upvote", "downvote", "withdraw"):
            raise _fail("vote must be 'upvote', 'downvote' or 'withdraw'")
        root = _metadata()
        lst = ET.SubElement(root, f"{entity}-list")
        for mbid, names in tags.items():
            el = ET.SubElement(lst, entity, id=check_mbid(mbid))
            tag_list = ET.SubElement(el, "user-tag-list")
            for name in _as_tag_list(names):
                attrib: dict[str, str] = {"vote": vote} if vote else {}
                tag = ET.SubElement(tag_list, "user-tag", attrib)
                ET.SubElement(tag, "name").text = name
        return self._submission("tag", root)

    def submit_ratings(self, entity: str, ratings: Mapping[str, int]) -> Call[None]:
        if entity not in consts.RATABLE_ENTITIES:
            raise _fail(f"cannot rate {entity}")
        root = _metadata()
        lst = ET.SubElement(root, f"{entity}-list")
        for mbid, rating in ratings.items():
            if not 0 <= int(rating) <= 100:
                raise _fail("ratings must be 0-100 (0 removes the rating)")
            el = ET.SubElement(lst, entity, id=check_mbid(mbid))
            ET.SubElement(el, "user-rating").text = str(int(rating))
        return self._submission("rating", root)

    def submit_barcodes(self, barcodes: Mapping[str, str]) -> Call[None]:
        root = _metadata()
        lst = ET.SubElement(root, "release-list")
        for mbid, barcode in barcodes.items():
            el = ET.SubElement(lst, "release", id=check_mbid(mbid))
            ET.SubElement(el, "barcode").text = check_gtin(barcode)
        return self._submission("release", root)

    def submit_isrcs(self, isrcs: Mapping[str, Iterable[str]]) -> Call[None]:
        root = _metadata()
        lst = ET.SubElement(root, "recording-list")
        for mbid, isrc_codes in isrcs.items():
            normalized = [normalize_isrc(c) for c in _as_list(isrc_codes)]
            el = ET.SubElement(lst, "recording", id=check_mbid(mbid))
            isrc_list = ET.SubElement(el, "isrc-list", count=str(len(normalized)))
            for code in normalized:
                ET.SubElement(isrc_list, "isrc", id=code)
        return self._submission("recording", root)


# ------------------------------------------------------------------ helpers


def _check_filter_values(types: list[str], statuses: list[str]) -> None:
    if bad := [t for t in types if t not in consts.RELEASE_TYPES]:
        raise _fail(
            f"invalid release type(s): {bad}. Allowed: {sorted(consts.RELEASE_TYPES)}"
        )
    if bad := [s for s in statuses if s not in consts.RELEASE_STATUSES]:
        raise _fail(
            f"invalid release status(es): {bad}. "
            f"Allowed: {sorted(consts.RELEASE_STATUSES)}"
        )


def _check_lookup_filters(
    incs: list[str], types: list[str], statuses: list[str]
) -> None:
    # On lookups, filters only apply to included releases / release groups.
    if types and not {"releases", "release-groups"} & set(incs):
        raise _fail("release_type requires inc 'releases' or 'release-groups'")
    if statuses and "releases" not in incs:
        raise _fail("release_status requires inc 'releases'")


def _check_browse_filters(entity: str, types: list[str], statuses: list[str]) -> None:
    if types and entity not in ("release", "release-group"):
        raise _fail("release_type only applies when browsing release (group)s")
    if statuses and entity != "release":
        raise _fail("release_status only applies when browsing releases")


def _link_name(key: str) -> str:
    return key if key == "track_artist" else key.replace("_", "-")


def _as_tag_list(names: Iterable[str]) -> list[str]:
    return [names] if isinstance(names, str) else list(names)


def _metadata() -> ET.Element:
    return ET.Element("metadata", xmlns=consts.MMD_NAMESPACE)


def _ignore(_: object) -> None:
    return None


def _page_parser(entity: str) -> Callable[[dict[str, Any]], models.Page[Any]]:
    model = models.ENTITY_MODELS[entity]
    list_key = consts.PLURALS[entity]

    def parse(data: dict[str, Any]) -> models.Page[Any]:
        items = [model.model_validate(x) for x in data.get(list_key, [])]
        count = data.get("count", data.get(f"{entity}-count", len(items)))
        offset = data.get("offset", data.get(f"{entity}-offset", 0))
        return models.Page(
            items=items,
            count=int(count),
            offset=int(offset),
            created=data.get("created"),
            raw=data,
        )

    return parse


@dataclass(frozen=True, slots=True)
class Binary:
    """A non-text response body, with what's needed to interpret it."""

    data: bytes
    content_type: str
    url: str


def _image_data(body: Binary) -> models.ImageData:
    return models.ImageData(
        data=body.data, content_type=body.content_type, url=body.url
    )


def decode_body(
    call: Call[Any], body: bytes, content_type: str = "", url: str = ""
) -> JSON | Binary:
    """Decode a successful response body according to ``call.accept``."""
    if call.accept == "json":
        return json.loads(body) if body else {}
    if call.accept == "bytes":
        return Binary(body, content_type, url)
    return body.decode("utf-8", errors="replace")


def xml_error_texts(text: str) -> list[str]:
    """The ``<text>`` messages of an ``<error><text>..</text></error>`` body.

    Error bodies never contain a DTD. Refusing any document that declares one
    rules out entity expansion ("billion laughs") and external entities (XXE)
    regardless of the expat version Python was built with.
    """
    if _DTD_RE.search(text):
        return []
    try:
        root = ET.fromstring(text)  # noqa: S314 - DTDs are rejected above
    except ET.ParseError:
        return []
    return [
        (el.text or "").strip()
        for el in root.iter()
        if el.tag.rpartition("}")[2] == "text"  # ignore any namespace
    ]


def raise_for_response(status: int, body: bytes, url: str, reason: str = "") -> None:
    """Raise the matching error if ``status`` is not 2xx.

    Raises:
        ResponseError: A subclass chosen by status code; see
            [`error_for_status`][musicbrainz.errors.error_for_status].
    """
    if codes.is_success(status):
        return
    message, help_ = reason or "error", None
    text = body.decode("utf-8", errors="replace").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            message, help_ = data.get("error", message), data.get("help")
        except ValueError:
            pass
    elif text[:20].lower().startswith(("<!doctype html", "<html")):
        # HTML error pages (e.g. the Cover Art Archive): the message is in <p>.
        if match := _HTML_P_RE.search(text):
            message = html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
    elif text.startswith("<"):
        texts = xml_error_texts(text)
        if texts:
            message, help_ = texts[0], (texts[1] if len(texts) > 1 else None)
    elif text and len(text) < 500:
        message = text
    raise error_for_status(status, message, help_=help_, url=url)


def retry_delay(
    status: int, retry_after: str | None, attempt: int, backoff: float
) -> float | None:
    """Seconds to wait before retrying, or ``None`` if not retryable."""
    if status not in RETRY_STATUSES:
        return None
    if retry_after:
        try:
            return min(float(retry_after), 60.0)
        except ValueError:
            pass
    return min(backoff * (2**attempt), 60.0)
