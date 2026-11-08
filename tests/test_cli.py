from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx2
import pytest
from httpx2 import codes
from typer.testing import CliRunner

from pymusicbrainz import MusicBrainz, cli
from pymusicbrainz import _constants as consts

ARTIST = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"
runner = CliRunner()


class FakeServer:
    def __init__(self, response: httpx2.Response) -> None:
        self.response = response
        self.requests: list[httpx2.Request] = []
        self.clients: list[MusicBrainz] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.response

    def params(self) -> dict[str, list[str]]:
        return parse_qs(self.requests[-1].url.query.decode())


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    """Route every client the CLI builds to an in-memory MockTransport."""
    fake = FakeServer(httpx2.Response(codes.OK, json={}))

    def factory(
        app: str, version: str, contact: str | None = None, *, base_url: str
    ) -> MusicBrainz:
        client = MusicBrainz(
            app,
            version,
            contact,
            base_url=base_url,
            rate_limit=None,
            transport=httpx2.MockTransport(fake.handle),
        )
        fake.clients.append(client)
        return client

    monkeypatch.setattr(cli, "MusicBrainz", factory)
    return fake


def invoke(*args: str) -> tuple[int, str]:
    result = runner.invoke(cli.app, list(args))
    return result.exit_code, result.output


def test_entity_constants_are_consistent() -> None:
    # Every lookup-able entity must also be browsable (one shared CLI choice).
    assert set(consts.BROWSE_LINKS) == set(consts.ENTITIES)
    assert set(consts.LOOKUP_INCLUDES) >= set(consts.ENTITIES)


def test_browse_link_options_cover_all_links() -> None:
    params = set(cli.browse.__annotations__)
    links = set().union(*consts.BROWSE_LINKS.values())
    assert {link.replace("-", "_") for link in links} <= params


def test_lookup(server: FakeServer) -> None:
    server.response = httpx2.Response(codes.OK, json={"id": ARTIST, "name": "Nirvana"})
    code, out = invoke(
        "--app", "MyApp", "--contact", "me@x", "lookup", "artist", ARTIST,
        "-i", "aliases", "--inc", "url-rels,tags",
    )  # fmt: skip
    assert code == 0, out
    assert json.loads(out) == {"id": ARTIST, "name": "Nirvana"}
    assert server.requests[0].url.path == f"/ws/2/artist/{ARTIST}"
    assert server.params()["inc"] == ["aliases+url-rels+tags"]
    assert server.requests[0].headers["User-Agent"].startswith("MyApp/")
    assert "( me@x )" in server.requests[0].headers["User-Agent"]


def test_search(server: FakeServer) -> None:
    server.response = httpx2.Response(
        codes.OK,
        json={"count": 1, "offset": 0, "release-groups": [{"id": ARTIST, "score": 9}]},
    )
    code, out = invoke("search", "release-group", "nevermind", "--limit", "3")
    assert code == 0, out
    assert json.loads(out)["items"] == [{"id": ARTIST, "score": 9}]
    assert server.params()["limit"] == ["3"]


def test_browse(server: FakeServer) -> None:
    server.response = httpx2.Response(
        codes.OK, json={"release-count": 1, "release-offset": 0, "releases": []}
    )
    code, out = invoke(
        "browse", "release", "--release-group", ARTIST, "--status", "official"
    )
    assert code == 0, out
    assert server.params()["release-group"] == [ARTIST]
    assert server.params()["status"] == ["official"]


def test_browse_all(server: FakeServer) -> None:
    server.response = httpx2.Response(
        codes.OK,
        json={"release-count": 1, "release-offset": 0, "releases": [{"id": ARTIST}]},
    )
    code, out = invoke("browse", "release", "--artist", ARTIST, "--all")
    assert code == 0, out
    assert json.loads(out) == [{"id": ARTIST}]


def test_other_commands(server: FakeServer) -> None:
    server.response = httpx2.Response(codes.OK, text="rock\npop\n")
    code, out = invoke("genres")
    assert code == 0, out
    assert json.loads(out) == ["rock", "pop"]

    server.response = httpx2.Response(codes.OK, json={"isrc": "USGF19942501"})
    assert invoke("isrc", "USGF19942501")[0] == 0

    server.response = httpx2.Response(codes.OK, json={"works": []})
    assert invoke("iswc", "T-070.243.691-6")[0] == 0

    server.response = httpx2.Response(codes.OK, json={"id": ARTIST, "resource": "x"})
    code, out = invoke("url", "https://example.com")
    assert code == 0, out
    assert json.loads(out) == [{"id": ARTIST, "resource": "x"}]

    server.response = httpx2.Response(codes.OK, json={"releases": []})
    code, out = invoke("discid", "-", "--toc", "1 1 1000 150", "--no-cdstubs")
    assert code == 0, out
    assert server.params()["cdstubs"] == ["no"]


def test_api_error_exits_1(server: FakeServer) -> None:
    server.response = httpx2.Response(codes.NOT_FOUND, json={"error": "Not Found"})
    code, out = invoke("lookup", "artist", ARTIST)
    assert code == 1
    assert "Not Found" in out


def test_validation_error_exits_1(server: FakeServer) -> None:
    code, out = invoke("lookup", "artist", "not-an-mbid")
    assert code == 1
    assert "invalid MBID" in out
    assert server.requests == []


def test_bad_choice_is_usage_error() -> None:
    code, _ = invoke("lookup", "spaceship", ARTIST)
    assert code == 2


def test_version_and_env(server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out = invoke("--version")
    assert code == 0
    assert out.strip() == cli.__version__

    monkeypatch.setenv("MUSICBRAINZ_URL", "https://mirror.example/ws/2")
    server.response = httpx2.Response(codes.OK, text="")
    invoke("genres")
    assert server.requests[-1].url.host == "mirror.example"


def test_verbose_logs_to_stderr_only(server: FakeServer) -> None:
    server.response = httpx2.Response(codes.OK, json={"id": ARTIST})
    result = runner.invoke(cli.app, ["-vv", "lookup", "artist", ARTIST])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"id": ARTIST}  # stdout stays pure JSON
    assert f"/ws/2/artist/{ARTIST}" in result.stderr
    assert "200 OK" in result.stderr


def test_quiet_by_default(server: FakeServer) -> None:
    server.response = httpx2.Response(codes.OK, json={"id": ARTIST})
    runner.invoke(cli.app, ["-vv", "lookup", "artist", ARTIST])
    result = runner.invoke(cli.app, ["lookup", "artist", ARTIST])
    assert result.exit_code == 0
    assert result.stderr == ""


def test_cover_art_listing(server: FakeServer) -> None:
    server.response = httpx2.Response(
        codes.OK, json={"images": [{"id": 1, "image": "u", "front": True}]}
    )
    code, out = invoke("cover-art", ARTIST, "--release-group")
    assert code == 0, out
    assert json.loads(out)["images"][0]["id"] == "1"
    assert "/release-group/" in str(server.requests[-1].url)
    code, _ = invoke("cover-art", ARTIST)
    assert code == 0
    assert "/release/" in str(server.requests[-1].url)


def test_cover_art_download(server: FakeServer, tmp_path: Path) -> None:
    server.response = httpx2.Response(
        codes.OK, content=b"\x89PNG", headers={"Content-Type": "image/png"}
    )
    target = tmp_path / "cover.png"
    code, out = invoke(
        "cover-art", ARTIST, "-o", str(target), "--image", "back", "--size", "500"
    )
    assert code == 0, out
    assert target.read_bytes() == b"\x89PNG"
    assert json.loads(out) == {
        "path": str(target),
        "bytes": 4,
        "content_type": "image/png",
        "url": f"https://coverartarchive.org/release/{ARTIST}/back-500",
    }


def test_cover_art_bad_size(server: FakeServer, tmp_path: Path) -> None:
    code, out = invoke("cover-art", ARTIST, "-o", str(tmp_path / "x"), "--size", "300")
    assert code == 1
    assert "size must be one of" in out
    assert server.requests == []
