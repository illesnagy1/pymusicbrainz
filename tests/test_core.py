"""Sans-IO core: request building, validation, response/error parsing.

These tests exercise :class:`Core` directly: no transport, no client.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import pytest
from _support import ARTIST, COLL, RELEASE
from httpx2 import codes

from pymusicbrainz import (
    AuthenticationError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    ResponseError,
    ServerError,
    ValidationError,
)
from pymusicbrainz import _constants as consts
from pymusicbrainz._core import (
    Call,
    Core,
    check_gtin,
    decode_body,
    normalize_iswc,
    raise_for_response,
    retry_delay,
)
from pymusicbrainz.errors import error_for_status

core = Core(client_id="App-1.0", has_auth=True)

# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: normalize_iswc("T-12"), "invalid ISWC"),
        (lambda: check_gtin("12345"), "must be 8/12/13/14 digits"),
        (lambda: check_gtin("abcdefghijkl"), "must be 8/12/13/14 digits"),
        (lambda: core.browse("release", {"artist": ARTIST}, offset=-1), "offset"),
        (lambda: core.browse("annotation", {"artist": ARTIST}), "cannot be browsed"),
        (
            lambda: core.browse("release", {"artist": ARTIST}, release_type="live"),
            None,
        ),
        (
            lambda: core.browse("artist", {"area": ARTIST}, release_type="album"),
            "release_type only applies",
        ),
        (
            lambda: core.browse(
                "release-group", {"artist": ARTIST}, release_group_status="nope"
            ),
            "release_group_status",
        ),
        (
            lambda: core.lookup("artist", ARTIST, "releases", None, "leaked"),
            "invalid release status",
        ),
        (
            lambda: core.lookup("artist", ARTIST, "release-groups", None, "official"),
            "release_status requires inc 'releases'",
        ),
        (lambda: core.lookup_urls([]), "at least one URL"),
        (lambda: core.lookup_urls(["u"] * 101), "at most 100 URLs"),
        (lambda: core.lookup_collection_contents("bad", "release"), "collection"),
        (lambda: core.lookup_collection_contents(COLL, "url"), "entity type"),
        (lambda: core.collection_modify("PUT", COLL, "release", []), "no MBIDs"),
        (
            lambda: core.collection_modify("PUT", COLL, "release", [RELEASE] * 401),
            "at most 400",
        ),
        (lambda: core.submit_tags("url", {ARTIST: ["x"]}), "cannot tag url"),
        (
            lambda: core.submit_tags("artist", {ARTIST: ["x"]}, "meh"),  # ty: ignore[invalid-argument-type]
            "vote must be",
        ),
        (lambda: core.submit_ratings("artist", {ARTIST: 101}), "0-100"),
        (lambda: core.submit_ratings("artist", {ARTIST: -1}), "0-100"),
        (lambda: core.search("genre", "rock"), "not searchable"),
    ],
)  # fmt: skip
def test_validation_errors(build: Callable[[], object], match: str | None) -> None:
    if match is None:  # sanity row: a valid combination must not raise
        build()
        return
    with pytest.raises(ValidationError, match=match):
        build()


def test_release_group_status_param() -> None:
    call = core.browse(
        "release-group", {"artist": ARTIST}, release_group_status="website-default"
    )
    assert ("release-group-status", "website-default") in call.params


def test_collection_summary_call() -> None:
    call = core.lookup_collection_contents(COLL, "releases")  # plural accepted too
    assert call.path == f"collection/{COLL}/releases"


def test_submit_tags_without_vote_has_no_attribute() -> None:
    call = core.submit_tags("artist", {ARTIST: "rock"})  # a bare string is one tag
    assert call.content is not None
    assert b'vote="' not in call.content
    assert b"<name>rock</name>" in call.content


def test_iswc_normalisation_variants() -> None:
    assert normalize_iswc(" t0702436916 ") == "T-070.243.691-6"
    assert normalize_iswc("T-070243691-6") == "T-070.243.691-6"


# ------------------------------------------------------------------ errors


@pytest.mark.parametrize(
    ("status", "cls"),
    [
        (codes.BAD_REQUEST, BadRequestError),
        (codes.UNAUTHORIZED, AuthenticationError),
        (codes.FORBIDDEN, ForbiddenError),
        (codes.NOT_FOUND, NotFoundError),
        (codes.TOO_MANY_REQUESTS, RateLimitError),
        (codes.SERVICE_UNAVAILABLE, RateLimitError),
        (codes.INTERNAL_SERVER_ERROR, ServerError),
        (codes.BAD_GATEWAY, ServerError),
        (codes.CONFLICT, ResponseError),
        (codes.MULTIPLE_CHOICES, ResponseError),
    ],
)
def test_error_for_status(status: int, cls: type[ResponseError]) -> None:
    err = error_for_status(status, "boom", help_="see docs", url="https://x")
    assert type(err) is cls
    assert err.status_code == status
    assert err.help == "see docs"
    assert str(err) == f"HTTP {status}: boom (https://x)"


def test_error_message_without_url() -> None:
    assert str(error_for_status(codes.NOT_FOUND, "gone")) == "HTTP 404: gone"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b'{"error": "Invalid mbid."}', "Invalid mbid."),
        (b"{not json", "Bad Request"),  # broken JSON falls back to the reason
        (b"plain text problem", "plain text problem"),
        (b"x" * 600, "Bad Request"),  # long non-JSON/XML bodies are not messages
        (b"", "Bad Request"),
        (b"<error><text>xml</text></error>", "xml"),
        (b"<html><body>gateway</body></html>", "Bad Request"),  # no <text>
    ],
)
def test_raise_for_response_messages(body: bytes, message: str) -> None:
    with pytest.raises(BadRequestError) as exc:
        raise_for_response(codes.BAD_REQUEST, body, "u", "Bad Request")
    assert exc.value.message == message


def test_raise_for_response_success_and_empty_reason() -> None:
    raise_for_response(codes.NO_CONTENT, b"", "u")  # 2xx: no exception
    with pytest.raises(ServerError) as exc:
        raise_for_response(codes.INTERNAL_SERVER_ERROR, b"", "u")
    assert exc.value.message == "error"


@pytest.mark.parametrize(
    ("status", "retry_after", "attempt", "expected"),
    [
        (codes.NOT_FOUND, None, 0, None),  # not retryable
        (codes.SERVICE_UNAVAILABLE, "3", 0, 3.0),
        (codes.SERVICE_UNAVAILABLE, "3600", 0, 60.0),  # capped
        (codes.SERVICE_UNAVAILABLE, None, 2, 4.0),  # backoff * 2**attempt
        (codes.SERVICE_UNAVAILABLE, None, 10, 60.0),  # capped
        # HTTP-date form is not parsed; falls back to exponential backoff.
        (codes.TOO_MANY_REQUESTS, "Wed, 21 Oct 2015 07:28:00 GMT", 1, 2.0),
    ],
)
def test_retry_delay(
    status: int, retry_after: str | None, attempt: int, expected: float | None
) -> None:
    assert retry_delay(status, retry_after, attempt, 1.0) == expected


# ------------------------------------------------------------------ Call plumbing


def _call(accept: Literal["json", "xml", "text"]) -> Call[object]:
    return Call("GET", "x", [("a", "1")], lambda d: d, accept=accept)


def test_call_format_params_and_headers() -> None:
    assert _call("json").request_params() == (("a", "1"), ("fmt", "json"))
    assert _call("text").request_params() == (("a", "1"), ("fmt", "txt"))
    assert _call("xml").request_params() == (("a", "1"),)
    assert _call("xml").request_headers() == {"Accept": "application/xml"}


def test_decode_body() -> None:
    assert decode_body(_call("json"), b"") == {}
    assert decode_body(_call("json"), b'{"a": 1}') == {"a": 1}
    assert decode_body(_call("text"), "é".encode()) == "é"


# ------------------------------------------------------------------ search fields


def test_search_field_typo_rejected() -> None:
    with pytest.raises(ValidationError, match=r"invalid search field.*artsit") as exc:
        core.search("artist", fields={"artsit": "Nirvana"})
    assert "Allowed: alias, area, arid" in str(exc.value)


def test_search_fields_skip_validation_when_disabled() -> None:
    call = Core(validate=False).search("artist", fields={"future_field": "x"})
    assert ("query", 'future_field:"x"') in call.params


def test_search_fields_cover_every_index() -> None:
    assert set(consts.SEARCH_FIELDS) == set(consts.SEARCH_ENTITIES)
    for entity, fields in consts.SEARCH_FIELDS.items():
        assert "tag" in fields or entity in {"annotation", "cdstub", "place", "url"}


# ------------------------------------------------------------- include dependencies


@pytest.mark.parametrize(
    ("entity", "bad", "fixed"),
    [
        ("artist", "isrcs", "recordings+isrcs"),
        ("artist", "media", "releases+media"),
        ("artist", "discids", "releases+discids"),
        ("artist", "various-artists", "releases+various-artists"),
        ("artist", "artist-credits", "release-groups+artist-credits"),
        ("label", "media", "releases+media"),
        ("label", "artist-credits", "release-rels+artist-credits"),
        ("recording", "discids", "releases+discids"),
        ("release-group", "media", "releases+media"),
        ("release", "isrcs", "recordings+isrcs"),
        ("release", "artist-rels+recording-level-rels",
         "recordings+artist-rels+recording-level-rels"),
        ("recording", "work-level-rels", "work-rels+work-level-rels"),
    ],
)  # fmt: skip
def test_dependent_includes(entity: str, bad: str, fixed: str) -> None:
    with pytest.raises(ValidationError, match=r"also needs|only modify"):
        core.lookup(entity, ARTIST, bad)
    core.lookup(entity, ARTIST, fixed)


def test_dependent_includes_message_names_the_fix() -> None:
    with pytest.raises(ValidationError) as exc:
        core.lookup("artist", ARTIST, "isrcs")
    assert str(exc.value) == "include 'isrcs' for artist also needs 'recordings'"


def test_includes_that_apply_directly_need_nothing() -> None:
    core.lookup("release", ARTIST, "media+discids+artist-credits")
    core.lookup("recording", ARTIST, "artist-credits+isrcs")


def test_dependent_includes_in_browse_and_discid() -> None:
    with pytest.raises(ValidationError, match="also needs 'recordings'"):
        core.browse("release", {"artist": ARTIST}, "isrcs")
    with pytest.raises(ValidationError, match="also needs 'recordings'"):
        core.lookup_discid("x" * 28, "isrcs")
    core.lookup_discid("x" * 28, "recordings+isrcs")


def test_dependency_suggestions_are_valid_includes() -> None:
    for entity, rules in consts.INCLUDE_DEPENDENCIES.items():
        allowed = consts.LOOKUP_INCLUDES[entity]
        for inc, needs in rules.items():
            assert inc in allowed, (entity, inc)
            assert needs <= allowed, (entity, inc, needs - allowed)


def test_dependent_includes_skipped_without_validation() -> None:
    call = Core(validate=False).lookup("artist", ARTIST, "isrcs")
    assert ("inc", "isrcs") in call.params
