"""Cover Art Archive support."""

from __future__ import annotations

import time

import anyio
import httpx2
import pytest
from _support import RELEASE, Recorder, make, make_async, ok, present
from httpx2 import codes

from musicbrainz import CoverArt, MusicBrainz, NotFoundError, ValidationError

RG = "1b022e01-4da6-387b-8658-8678046e4cef"
CAA = f"https://coverartarchive.org/release/{RELEASE}"
LISTING = {
    "images": [
        {
            "id": 45224054828,  # a number in practice, a string in the docs
            "image": f"{CAA}/45224054828.jpg",
            "thumbnails": {"250": f"{CAA}/45224054828-250.jpg", "small": "x"},
            "types": ["Front"],
            "front": True,
            "back": False,
            "approved": True,
            "comment": "",
            "edit": 146679097,
        },
        {"id": "12750224075", "image": f"{CAA}/12750224075.png", "back": True},
    ],
    "release": f"https://musicbrainz.org/release/{RELEASE}",
}


def test_listing_model() -> None:
    art = CoverArt.model_validate(LISTING)
    front, back = present(art.front), present(art.back)
    assert front.id == "45224054828"
    assert front.thumbnail(250) == f"{CAA}/45224054828-250.jpg"
    assert front.thumbnail(1200) is None  # thumbnails may be missing
    assert back.id == "12750224075"
    assert back.types == []
    assert CoverArt().front is None


def test_release_listing_follows_redirect() -> None:
    rec = Recorder(
        httpx2.Response(
            codes.TEMPORARY_REDIRECT,
            headers={"Location": "https://archive.org/download/x/index.json"},
        ),
        ok(LISTING),
    )
    with make(rec) as mb:
        art = mb.cover_art.release(RELEASE)
    assert len(art.images) == 2
    first, second = rec.requests
    assert str(first.url) == CAA  # no fmt=json: not a MusicBrainz WS/2 request
    assert first.headers["Accept"] == "application/json"
    assert second.url.host == "archive.org"


def test_release_group_listing() -> None:
    rec = Recorder(ok(LISTING))
    with make(rec) as mb:
        mb.cover_art.release_group(RG)
    assert str(rec.last.url) == f"https://coverartarchive.org/release-group/{RG}"


def test_image_download() -> None:
    rec = Recorder(
        httpx2.Response(
            codes.TEMPORARY_REDIRECT,
            headers={"Location": "https://ia.example.org/front_thumb250.jpg"},
        ),
        httpx2.Response(
            codes.OK, content=b"\xff\xd8jpeg", headers={"Content-Type": "image/jpeg"}
        ),
    )
    with make(rec) as mb:
        img = mb.cover_art.front(RELEASE, size=250)
    assert img.data == b"\xff\xd8jpeg"
    assert img.content_type == "image/jpeg"
    assert img.url == "https://ia.example.org/front_thumb250.jpg"
    assert str(rec.requests[0].url) == f"{CAA}/front-250"
    assert rec.requests[0].headers["Accept"] == "image/*"


@pytest.mark.parametrize(
    ("image", "size", "release_group", "path"),
    [
        ("front", None, False, f"release/{RELEASE}/front"),
        ("back", 1200, False, f"release/{RELEASE}/back-1200"),
        (45224054828, 500, False, f"release/{RELEASE}/45224054828-500"),
        ("45224054828.jpg", 250, False, f"release/{RELEASE}/45224054828-250.jpg"),
        ("front", 500, True, f"release-group/{RELEASE}/front-500"),
    ],
)
def test_image_urls(
    image: str | int, size: int | None, release_group: bool, path: str
) -> None:
    with make(Recorder(ok({}))) as mb:
        url = mb.cover_art.url(
            RELEASE,
            image,
            size=size,  # ty: ignore[invalid-argument-type]
            release_group=release_group,
        )
    assert url == f"https://coverartarchive.org/{path}"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"image": "back", "release_group": True}, "only have a 'front'"),
        ({"image": "spine"}, "image must be"),
        ({"size": 300}, "size must be one of"),
    ],
)
def test_image_validation(kwargs: dict[str, object], match: str) -> None:
    with make(Recorder(ok({}))) as mb, pytest.raises(ValidationError, match=match):
        mb.cover_art.url(RELEASE, **kwargs)  # ty: ignore[invalid-argument-type]
    with make(Recorder(ok({}))) as mb, pytest.raises(ValidationError, match="MBID"):
        mb.cover_art.release("not-an-mbid")


def test_not_found_message_from_html() -> None:
    page = (
        "<!doctype html>\n<html lang=en>\n<title>404 Not Found</title>\n"
        "<h1>Not Found</h1>\n<p>No cover art found for release &lt;x&gt;</p>\n"
    )
    rec = Recorder(httpx2.Response(codes.NOT_FOUND, text=page))
    with make(rec) as mb, pytest.raises(NotFoundError) as exc:
        mb.cover_art.release(RELEASE)
    assert exc.value.message == "No cover art found for release <x>"


def test_credentials_never_sent_to_cover_art_archive() -> None:
    rec = Recorder(ok(LISTING), ok({"id": RELEASE}))
    with make(rec, oauth_token="secret") as mb:
        mb.cover_art.release(RELEASE)
        mb.artists.get(RELEASE)
    caa, mbz = rec.requests
    assert "Authorization" not in caa.headers
    assert mbz.headers["Authorization"] == "Bearer secret"


def test_cover_art_bypasses_musicbrainz_rate_limit() -> None:
    rec = Recorder(ok(LISTING))
    mb = MusicBrainz("A", "1", transport=httpx2.MockTransport(rec), rate_limit=1)
    with mb:
        start = time.monotonic()
        for _ in range(3):
            mb.cover_art.release(RELEASE)
        assert time.monotonic() - start < 0.5  # 1 req/s would take >= 2 s


def test_async_cover_art() -> None:
    async def main() -> None:
        rec = Recorder(
            ok(LISTING),
            ok(LISTING),
            httpx2.Response(
                codes.OK, content=b"png", headers={"Content-Type": "image/png"}
            ),
        )
        async with make_async(rec) as mb:
            assert len((await mb.cover_art.release(RELEASE)).images) == 2
            assert (await mb.cover_art.release_group(RG)).release
            assert (await mb.cover_art.back(RELEASE)).content_type == "image/png"
            assert mb.cover_art.url(RELEASE).endswith("/front")
        assert str(rec.last.url) == f"{CAA}/back"

    anyio.run(main)


def test_async_front_and_image() -> None:
    async def main() -> None:
        rec = Recorder(httpx2.Response(codes.OK, content=b"x"))
        async with make_async(rec) as mb:
            await mb.cover_art.front(RG, size=250, release_group=True)
            await mb.cover_art.image(RELEASE, 123)
        assert [str(r.url) for r in rec.requests] == [
            f"https://coverartarchive.org/release-group/{RG}/front-250",
            f"{CAA}/123",
        ]

    anyio.run(main)


def test_sync_back_and_custom_caa_url() -> None:
    rec = Recorder(httpx2.Response(codes.OK, content=b"x"))
    mb = MusicBrainz(
        "A",
        "1",
        caa_url="https://caa.mirror.example",  # trailing slash added
        transport=httpx2.MockTransport(rec),
        rate_limit=None,
    )
    with mb:
        mb.cover_art.back(RELEASE, size=500)
        mb.cover_art.image(RELEASE, "front")
    assert str(rec.requests[0].url) == (
        f"https://caa.mirror.example/release/{RELEASE}/back-500"
    )
