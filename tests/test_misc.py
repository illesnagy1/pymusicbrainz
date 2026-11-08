"""Models, query helpers, rate limiters, and CLI plumbing not covered elsewhere."""

from __future__ import annotations

import logging
import sys
import time
from importlib.util import find_spec

import anyio
import httpx2
import pytest
from _support import ARTIST
from rich.logging import RichHandler

import musicbrainz
from musicbrainz import (
    AsyncMusicBrainz,
    AsyncRateLimiter,
    Collection,
    MusicBrainz,
    Page,
    RateLimiter,
    Recording,
    Relation,
    ReleaseGroup,
    _ratelimit,
    cli,
)
from musicbrainz import client as client_module
from musicbrainz.models import ArtistCredit, credit_string
from musicbrainz.query import any_of, build_query

# ------------------------------------------------------------------ models


def test_relation_target() -> None:
    assert Relation().target is None
    rel = Relation.model_validate({"target-type": "release-group",
                                   "release-group": {"id": ARTIST}})  # fmt: skip
    assert rel.target is rel.release_group


def test_artist_credit_phrases() -> None:
    credit = [
        {"name": "A", "joinphrase": " feat. ", "artist": {"id": ARTIST, "name": "x"}},
        {"joinphrase": "", "artist": {"id": ARTIST, "name": "B (fallback)"}},
    ]
    rec = Recording.model_validate({"id": ARTIST, "artist-credit": credit})
    rg = ReleaseGroup.model_validate({"id": ARTIST, "artist-credit": credit})
    assert rec.artist_credit_phrase == rg.artist_credit_phrase == "A feat. B (fallback)"
    assert credit_string([ArtistCredit(name=None, artist=None)]) == ""


def test_collection_item_count() -> None:
    assert Collection(id=ARTIST).item_count is None
    with_other_extra = Collection.model_validate({"id": ARTIST, "foo": "bar"})
    assert with_other_extra.item_count is None
    assert Collection.model_validate({"id": ARTIST, "work-count": 4}).item_count == 4


def test_page_protocol() -> None:
    page = Page(items=["a", "b"], count=5, offset=0)
    assert list(page) == ["a", "b"]
    assert page[1] == "b"
    assert len(page) == 2
    assert page.has_more
    assert page.next_offset == 2
    assert not Page(items=[], count=5, offset=5).has_more  # empty page stops paging


def test_models_round_trip_to_kebab_case() -> None:
    rg = ReleaseGroup(id=ARTIST, first_release_date="1991")
    dumped = rg.model_dump(exclude_defaults=True)
    assert dumped == {"id": ARTIST, "first-release-date": "1991"}
    assert ReleaseGroup.model_validate(dumped) == rg


# ------------------------------------------------------------------ query


def test_query_helpers() -> None:
    assert any_of(["a b", 'q"t']) == '("a b" OR "q\\"t")'
    assert build_query(None, {"artist": "X"}, operator="OR", tag="y") == (
        'artist:"X" OR tag:"y"'
    )


# ------------------------------------------------------------------ rate limiting


@pytest.mark.parametrize("cls", [RateLimiter, AsyncRateLimiter])
@pytest.mark.parametrize("rps", [0, -1])
def test_rate_limiter_rejects_non_positive(
    cls: type[RateLimiter | AsyncRateLimiter], rps: float
) -> None:
    with pytest.raises(ValueError, match="positive"):
        cls(rps)


def test_async_rate_limiter_spacing() -> None:
    limiter = AsyncRateLimiter(requests_per_second=20)

    async def burst() -> float:
        start = time.monotonic()
        for _ in range(4):
            await limiter.acquire()
        return time.monotonic() - start

    assert anyio.run(burst) >= 0.14  # 3 gaps of 50 ms


def test_shared_rate_limiter_across_clients() -> None:
    limiter = RateLimiter(requests_per_second=20)
    start = time.monotonic()
    for _ in range(2):  # two "clients" drawing from one budget
        limiter.acquire()
        limiter.acquire()
    assert time.monotonic() - start >= 0.14


# ------------------------------------------------------------------ CLI plumbing


def test_dump_falls_back_to_str() -> None:
    assert cli._dump(None) is None  # noqa: SLF001
    assert cli._dump({"a": 1}) == {"a": 1}  # noqa: SLF001
    assert cli._dump(3.5) == 3.5  # noqa: SLF001
    obj = object()
    assert cli._dump(obj) == str(obj)  # noqa: SLF001


def test_emit_pretty_prints_on_a_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    cli._emit({"name": "Björk"})  # noqa: SLF001
    out = capsys.readouterr().out
    assert "\x1b[" in out  # rich's coloured JSON, not plain json.dump
    assert '"Björk"' in out  # non-ASCII kept


def test_logging_uses_rich_on_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    log = logging.getLogger("musicbrainz")
    try:
        cli._setup_logging(1)  # noqa: SLF001
        assert any(isinstance(h, RichHandler) for h in log.handlers)
        assert log.level == logging.WARNING
    finally:
        cli._setup_logging(0)  # noqa: SLF001
    assert not any(isinstance(h, RichHandler) for h in log.handlers)
    assert log.level == logging.NOTSET


def test_console_entry_point(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        musicbrainz.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == musicbrainz.__version__


def test_tiny_rate_limit_waits_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="musicbrainz"):
        _ratelimit._log_wait(0.001)  # noqa: SLF001
        _ratelimit._log_wait(0.5)  # noqa: SLF001
    assert [r.getMessage() for r in caplog.records] == ["rate limit: waiting 0.50s"]


# ------------------------------------------------------------------ proxy


def test_proxy_passed_to_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def capture(real: type) -> object:
        def factory(**kwargs: object) -> object:
            seen.append(kwargs["proxy"])
            return real(**kwargs)

        return factory

    monkeypatch.setattr(client_module.httpx2, "Client", capture(httpx2.Client))
    monkeypatch.setattr(
        client_module.httpx2, "AsyncClient", capture(httpx2.AsyncClient)
    )
    MusicBrainz("A", "1", proxy="http://proxy.example:3128").close()
    AsyncMusicBrainz("A", "1", proxy="http://proxy.example:8080")
    MusicBrainz("A", "1").close()
    assert seen == ["http://proxy.example:3128", "http://proxy.example:8080", None]


@pytest.mark.skipif(find_spec("socksio") is not None, reason="socks extra installed")
def test_socks_without_extra_gives_our_hint() -> None:
    with pytest.raises(ImportError, match=r"musicbrainz\[socks\]"):
        MusicBrainz("A", "1", proxy="socks5://proxy.example:1080")


def test_other_import_errors_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(**_: object) -> None:
        raise ImportError("something else")

    monkeypatch.setattr(client_module.httpx2, "Client", broken)
    with pytest.raises(ImportError, match="something else"):
        MusicBrainz("A", "1")


def test_console_script_without_cli_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "musicbrainz.cli")  # force a fresh import
    monkeypatch.setitem(sys.modules, "typer", None)  # as if typer weren't installed
    with pytest.raises(SystemExit) as exc:
        musicbrainz.main(["--version"])
    assert "pip install 'musicbrainz[cli]'" in str(exc.value.code)
    assert "typer is missing" in str(exc.value.code)
