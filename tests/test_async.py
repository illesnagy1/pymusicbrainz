"""AsyncMusicBrainz: the async twin of the sync client must behave identically."""

from __future__ import annotations

import logging

import httpx2
import pytest
from _support import ARTIST, COLL, RELEASE, Recorder, make_async, ok
from httpx2 import codes

from musicbrainz import (
    AsyncMusicBrainz,
    AuthenticationRequiredError,
    CDStub,
    Disc,
    NetworkError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_lookup_search_browse() -> None:
    rec = Recorder(
        ok({"id": ARTIST, "name": "Nirvana"}),
        ok({"count": 1, "offset": 0, "artists": [{"id": ARTIST, "score": 100}]}),
        ok({"release-count": 1, "release-offset": 0, "releases": [{"id": RELEASE}]}),
    )
    async with make_async(rec) as mb:
        assert (await mb.artists.get(ARTIST, inc="aliases")).name == "Nirvana"
        assert (await mb.artists.search(artist="Nirvana"))[0].score == 100
        page = await mb.releases.browse(artist=ARTIST, release_status="official")
        assert page.count == 1
    assert rec.params(2)["status"] == ["official"]
    assert "AsyncEntityResource release" in repr(mb.releases)


async def test_iter_browse_pages() -> None:
    rec = Recorder(
        ok(
            {"release-count": 3, "release-offset": 0, "releases": [{"id": RELEASE}] * 2}
        ),
        ok({"release-count": 3, "release-offset": 2, "releases": [{"id": RELEASE}]}),
    )
    async with make_async(rec) as mb:
        items = [r async for r in mb.releases.iter_browse(artist=ARTIST)]
    assert len(items) == 3
    assert rec.params(1)["offset"] == ["2"]


async def test_iter_search_max_items_and_eager_validation() -> None:
    rec = Recorder(ok({"count": 500, "offset": 0, "artists": [{"id": ARTIST}] * 100}))
    async with make_async(rec) as mb:
        got = [a async for a in mb.artists.iter_search("x", max_items=5)]
        assert len(got) == 5
        with pytest.raises(ValidationError):
            mb.releases.iter_browse(artist="not-an-mbid")  # raises before awaiting
    assert len(rec.requests) == 1


async def test_non_mbid_lookups() -> None:
    rec = Recorder(
        ok({"id": "x" * 28, "offsets": [150], "releases": [{"id": RELEASE}]}),
        ok({"id": "y" * 28, "title": "Stub"}),
        ok({"isrc": "USGF19942501", "recordings": [{"id": ARTIST}]}),
        ok({"works": [{"id": ARTIST, "title": "W"}]}),
    )
    async with make_async(rec) as mb:
        assert isinstance(await mb.lookup_discid("x" * 28), Disc)
        assert isinstance(await mb.lookup_discid("y" * 28), CDStub)
        assert (await mb.lookup_isrc("USGF19942501")).recordings
        assert (await mb.lookup_iswc("T-070.243.691-6"))[0].title == "W"


async def test_urls() -> None:
    rec = Recorder(
        ok({"id": ARTIST, "resource": "https://a"}),
        ok({"urls": [{"id": ARTIST, "resource": "https://a"}]}),
        httpx2.Response(codes.NOT_FOUND, json={"error": "Not Found"}),
    )
    async with make_async(rec) as mb:
        assert (await mb.urls.by_resource("https://a")).resource == "https://a"
        assert len(await mb.urls.by_resources(["https://a", "https://b"])) == 1
        assert await mb.urls.by_resources(["https://unknown"]) == []
        # A 404 for a multi-URL batch is a real error, not "skip unknown".
        with pytest.raises(NotFoundError):
            await mb.urls.by_resources(["https://a", "https://b"])


async def test_genres() -> None:
    rec = Recorder(
        ok({"genre-count": 2, "genre-offset": 0, "genres": [{"name": "rock"}] * 2}),
        ok({"genre-count": 2, "genre-offset": 0, "genres": [{"name": "rock"}] * 2}),
        httpx2.Response(codes.OK, text="rock\npop\n"),
    )
    async with make_async(rec) as mb:
        assert (await mb.genres.all(limit=2)).count == 2
        assert len([g async for g in mb.genres.iter_all()]) == 2
        assert await mb.genres.names() == ["rock", "pop"]


async def test_collections() -> None:
    rec = Recorder(
        ok({"collection-count": 1, "collection-offset": 0, "collections": [
            {"id": COLL, "release-count": 3}]}),
        ok({"id": COLL, "name": "Mine", "release-count": 3}),
        ok({"release-count": 1, "release-offset": 0, "releases": [{"id": RELEASE}]}),
        ok({"release-count": 1, "release-offset": 0, "releases": [{"id": RELEASE}]}),
        httpx2.Response(codes.OK, text="<metadata/>"),
    )  # fmt: skip
    async with make_async(rec, oauth_token="t") as mb:
        assert (await mb.collections.of_editor("me"))[0].item_count == 3
        assert (await mb.collections.summary(COLL, "release")).name == "Mine"
        assert len(await mb.collections.items(COLL, "releases")) == 1
        assert len([r async for r in mb.collections.iter_items(COLL, "release")]) == 1
        await mb.collections.add(COLL, "release", [RELEASE])
        await mb.collections.remove(COLL, "release", [RELEASE])
    assert [r.method for r in rec.requests[-2:]] == ["PUT", "DELETE"]


async def test_submissions_and_raw_request() -> None:
    rec = Recorder(httpx2.Response(codes.OK, text="<metadata/>"))
    async with make_async(rec, oauth_token="t") as mb:
        await mb.submit_tags("artist", {ARTIST: ["rock"]}, vote="upvote")
        await mb.submit_ratings("artist", {ARTIST: 80})
        await mb.submit_barcodes({RELEASE: "4006381333931"})
        await mb.submit_isrcs({ARTIST: ["USGF19942501"]})
    paths = [r.url.path.removeprefix("/ws/2/") for r in rec.requests]
    assert paths == ["tag", "rating", "release", "recording"]

    rec = Recorder(ok({"anything": 1}))
    async with make_async(rec) as mb:
        assert await mb.request("GET", "/artist", {"query": "x"}) == {"anything": 1}


async def test_submissions_require_auth() -> None:
    async with make_async(Recorder(ok({}))) as mb:
        with pytest.raises(AuthenticationRequiredError):
            await mb.submit_ratings("artist", {ARTIST: 20})


async def test_resource_lookup() -> None:
    async with make_async(Recorder(ok({}))) as mb:
        assert mb.resource("release-groups") is mb.release_groups
        with pytest.raises(ValidationError, match="unknown entity"):
            mb.resource("spaceship")


async def test_retries_and_logging(caplog: pytest.LogCaptureFixture) -> None:
    rec = Recorder(
        httpx2.Response(codes.SERVICE_UNAVAILABLE),
        httpx2.ConnectError("refused"),
        ok({"id": ARTIST}),
    )
    async with make_async(rec) as mb:
        with caplog.at_level(logging.DEBUG, logger="musicbrainz"):
            await mb.artists.get(ARTIST)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert len(rec.requests) == 3


async def test_retries_exhausted_and_network_errors() -> None:
    async with make_async(
        Recorder(httpx2.Response(codes.SERVICE_UNAVAILABLE)), max_retries=1
    ) as mb:
        with pytest.raises(RateLimitError):
            await mb.artists.get(ARTIST)

    rec = Recorder(httpx2.ReadTimeout("slow"))
    async with make_async(rec, max_retries=1) as mb:
        with pytest.raises(NetworkError, match="slow"):
            await mb.artists.get(ARTIST)
    assert len(rec.requests) == 2

    # Submissions are not replayed after a read error (may have been applied).
    rec = Recorder(httpx2.ReadTimeout("slow"))
    async with make_async(rec, oauth_token="t") as mb:
        with pytest.raises(NetworkError):
            await mb.submit_ratings("artist", {ARTIST: 20})
    assert len(rec.requests) == 1


async def test_rate_limited_client_uses_async_limiter() -> None:
    rec = Recorder(ok({"id": ARTIST}))
    async with AsyncMusicBrainz(
        "A", "1", transport=httpx2.MockTransport(rec), rate_limit=50
    ) as mb:
        for _ in range(3):
            await mb.artists.get(ARTIST)
    assert len(rec.requests) == 3
