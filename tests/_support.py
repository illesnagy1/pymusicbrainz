"""Shared test helpers (imported by the test modules, not a test module itself)."""

from urllib.parse import parse_qs

import httpx2
from httpx2 import codes

from musicbrainz import AsyncMusicBrainz, MusicBrainz

ARTIST = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"
RELEASE = "a4864e94-6d75-4ade-bc93-0dabf3521453"
COLL = "f4784850-3844-11e0-9e42-0800200c9a66"


class Recorder:
    """MockTransport handler recording requests.

    Replies are taken from a queue; the last one repeats forever.
    """

    def __init__(self, *replies: httpx2.Response | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def last(self) -> httpx2.Request:
        return self.requests[-1]

    def params(self, i: int = -1) -> dict[str, list[str]]:
        return parse_qs(self.requests[i].url.query.decode())


def present[T](value: T | None) -> T:
    """Narrow an Optional for the type checker, failing the test if it is None."""
    assert value is not None
    return value


def make(
    handler: Recorder,
    *,
    rate_limit: float | None = None,
    max_retries: int = 3,
    validate: bool = True,
    username: str | None = None,
    password: str | None = None,
    oauth_token: str | None = None,
) -> MusicBrainz:
    return MusicBrainz(
        "TestApp",
        "1.0",
        "test@example.com",
        transport=httpx2.MockTransport(handler),
        rate_limit=rate_limit,
        max_retries=max_retries,
        backoff_factor=0,
        validate=validate,
        username=username,
        password=password,
        oauth_token=oauth_token,
    )


def ok(data: object) -> httpx2.Response:
    return httpx2.Response(codes.OK, json=data)


def make_async(
    handler: Recorder,
    *,
    max_retries: int = 3,
    oauth_token: str | None = None,
) -> AsyncMusicBrainz:
    return AsyncMusicBrainz(
        "TestApp",
        "1.0",
        "test@example.com",
        transport=httpx2.MockTransport(handler),
        rate_limit=None,
        max_retries=max_retries,
        backoff_factor=0,
        oauth_token=oauth_token,
    )
