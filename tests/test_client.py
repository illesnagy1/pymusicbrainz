import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, unquote

import anyio
import httpx2
import pytest
from httpx2 import codes

from musicbrainz import (
    AsyncMusicBrainz,
    AuthenticationRequiredError,
    BadRequestError,
    CDStub,
    Disc,
    MusicBrainz,
    NetworkError,
    NotFoundError,
    RateLimitError,
    ValidationError,
    build_query,
    escape,
)
from musicbrainz._core import (
    check_gtin,
    normalize_isrc,
    normalize_iswc,
    xml_error_texts,
)

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


# ------------------------------------------------------------------ lookups


def test_lookup_builds_url_and_parses() -> None:
    rec = Recorder(
        ok(
            {
                "id": ARTIST,
                "name": "Nirvana",
                "sort-name": "Nirvana",
                "life-span": {"begin": "1987", "ended": True},
                "aliases": [
                    {"name": "Nirvana US", "sort-name": "Nirvana US", "primary": None}
                ],
                "relations": [
                    {
                        "type": "official homepage",
                        "target-type": "url",
                        "direction": "forward",
                        "url": {"id": "u1", "resource": "https://nirvana.com"},
                    }
                ],
                "rating": {"value": 4.5, "votes-count": 10},
                "something-new": 42,
            }
        )
    )
    with make(rec) as mb:
        a = mb.artists.get(ARTIST, inc=["aliases", "url-rels", "ratings"])
    assert rec.last.method == "GET"
    assert rec.last.url.path == f"/ws/2/artist/{ARTIST}"
    assert rec.params() == {"inc": ["aliases+url-rels+ratings"], "fmt": ["json"]}
    assert rec.last.headers["User-Agent"].startswith(
        "TestApp/1.0 ( test@example.com ) python-musicbrainz/"
    )
    assert a.name == "Nirvana"
    assert a.sort_name == "Nirvana"
    assert present(a.life_span).ended
    assert present(a.rating).votes_count == 10
    assert present(a.relations[0].url).resource == "https://nirvana.com"
    assert a.relations[0].target is a.relations[0].url
    assert a.relations_of("url", "official homepage")
    assert present(a.model_extra)["something-new"] == 42


def test_lookup_validation() -> None:
    rec = Recorder(ok({}))
    with make(rec) as mb:
        with pytest.raises(ValidationError, match="invalid MBID"):
            mb.artists.get("not-an-mbid")
        with pytest.raises(ValidationError, match="invalid include"):
            mb.artists.get(ARTIST, inc="labels")
        with pytest.raises(ValidationError, match="requires inc"):
            mb.artists.get(ARTIST, release_type="album")
        with pytest.raises(ValidationError, match="invalid release type"):
            mb.artists.get(ARTIST, inc="releases", release_type="lp")
        with pytest.raises(AuthenticationRequiredError):
            mb.artists.get(ARTIST, inc="user-tags")
    assert rec.requests == []


def test_validation_can_be_disabled() -> None:
    rec = Recorder(ok({"id": ARTIST}))
    with make(rec, validate=False) as mb:
        mb.artists.get(ARTIST, inc="future-include")
    assert rec.params()["inc"] == ["future-include"]


def test_lookup_filters_and_string_includes() -> None:
    rec = Recorder(
        ok({"id": ARTIST, "release-groups": [{"id": RELEASE, "title": "Nevermind"}]})
    )
    with make(rec) as mb:
        a = mb.artists.get(
            ARTIST,
            inc="releases release-groups",
            release_type=["Album", "ep"],
            release_status="official",
        )
    assert rec.params()["type"] == ["album|ep"]
    assert rec.params()["status"] == ["official"]
    assert a.release_groups[0].title == "Nevermind"


def test_release_lookup_nested() -> None:
    rec = Recorder(
        ok(
            {
                "id": RELEASE,
                "title": "Nevermind",
                "artist-credit": [
                    {
                        "name": "Simon",
                        "joinphrase": " & ",
                        "artist": {"id": ARTIST, "name": "Paul Simon"},
                    },
                    {
                        "name": "Garfunkel",
                        "joinphrase": "",
                        "artist": {"id": ARTIST, "name": "Art Garfunkel"},
                    },
                ],
                "label-info": [
                    {
                        "catalog-number": "DGCD-24425",
                        "label": {"id": ARTIST, "name": "DGC"},
                    }
                ],
                "media": [
                    {
                        "position": 1,
                        "format": "CD",
                        "track-count": 1,
                        "discs": [{"id": "x", "sectors": 1, "offsets": [150]}],
                        "tracks": [
                            {
                                "id": "t",
                                "number": "1",
                                "position": 1,
                                "title": "SLTS",
                                "length": 301000,
                                "recording": {"id": ARTIST, "title": "SLTS"},
                            }
                        ],
                    }
                ],
                "cover-art-archive": {"front": True, "count": 3},
            }
        )
    )
    with make(rec) as mb:
        r = mb.releases.get(
            RELEASE, inc=["recordings", "artist-credits", "labels", "discids", "media"]
        )
    assert r.artist_credit_phrase == "Simon & Garfunkel"
    assert r.label_info[0].catalog_number == "DGCD-24425"
    assert present(r.media[0].tracks[0].recording).title == "SLTS"
    assert r.media[0].discs[0].offsets == [150]
    assert present(r.cover_art_archive).front


# ------------------------------------------------------------------ browse / pagination


def test_browse_params_and_page() -> None:
    rec = Recorder(
        ok(
            {
                "release-count": 3,
                "release-offset": 0,
                "releases": [{"id": RELEASE, "title": "A"}],
            }
        )
    )
    with make(rec) as mb:
        page = mb.releases.browse(
            artist=ARTIST,
            release_type="live",
            release_status="bootleg",
            limit=1,
            inc="media",
        )
    assert rec.params() == {
        "artist": [ARTIST],
        "inc": ["media"],
        "limit": ["1"],
        "type": ["live"],
        "status": ["bootleg"],
        "fmt": ["json"],
    }
    assert page.count == 3
    assert len(page) == 1
    assert page.has_more
    assert page.next_offset == 1


def test_browse_validation() -> None:
    with make(Recorder(ok({}))) as mb:
        with pytest.raises(ValidationError, match="exactly one"):
            mb.releases.browse()
        with pytest.raises(ValidationError, match="exactly one"):
            mb.releases.browse(artist=ARTIST, label=ARTIST)
        with pytest.raises(ValidationError, match="cannot browse"):
            mb.artists.browse(label=ARTIST)
        with pytest.raises(ValidationError, match="limit"):
            mb.releases.browse(artist=ARTIST, limit=500)
        with pytest.raises(ValidationError, match="release_status"):
            mb.release_groups.browse(artist=ARTIST, release_status="official")
        # eager validation for iterators
        with pytest.raises(ValidationError):
            mb.releases.iter_browse(artist="bad")


def test_browse_kwarg_name_mapping() -> None:
    rec = Recorder(ok({"release-count": 0, "release-offset": 0, "releases": []}))
    with make(rec) as mb:
        mb.releases.browse(release_group=RELEASE)
        mb.releases.browse(track_artist=ARTIST)
    assert "release-group" in rec.params(0)
    assert "track_artist" in rec.params(1)


def test_iter_browse_advances_by_returned_items() -> None:
    pages = [
        ok(
            {
                "release-count": 5,
                "release-offset": 0,
                "releases": [{"id": RELEASE, "title": str(i)} for i in range(2)],
            }
        ),
        ok(
            {
                "release-count": 5,
                "release-offset": 2,
                "releases": [{"id": RELEASE, "title": str(i)} for i in range(2, 4)],
            }
        ),
        ok(
            {
                "release-count": 5,
                "release-offset": 4,
                "releases": [{"id": RELEASE, "title": "4"}],
            }
        ),
    ]
    rec = Recorder(*pages)
    with make(rec) as mb:
        titles = [r.title for r in mb.releases.iter_browse(artist=ARTIST)]
    assert titles == ["0", "1", "2", "3", "4"]
    assert [rec.params(i).get("offset") for i in range(3)] == [["0"], ["2"], ["4"]]


def test_iter_max_items_stops_early() -> None:
    rec = Recorder(
        ok({"count": 1000, "offset": 0, "artists": [{"id": ARTIST, "name": "x"}] * 100})
    )
    with make(rec) as mb:
        assert len(list(mb.artists.iter_search("x", max_items=150))) == 150
    assert len(rec.requests) == 2


# ------------------------------------------------------------------ search


def test_search() -> None:
    rec = Recorder(
        ok(
            {
                "created": "2026-01-01T00:00:00Z",
                "count": 1,
                "offset": 0,
                "release-groups": [
                    {
                        "id": RELEASE,
                        "title": "Nevermind",
                        "score": 100,
                        "primary-type": "Album",
                    }
                ],
            }
        )
    )
    with make(rec) as mb:
        page = mb.release_groups.search(
            "nevermind", artist="Nirvana", primarytype="album", limit=5, dismax=True
        )
    q = rec.params()
    assert q["query"] == ['(nevermind) AND artist:"Nirvana" AND primarytype:"album"']
    assert q["dismax"] == ["true"]
    assert q["limit"] == ["5"]
    assert page[0].score == 100
    assert page[0].primary_type == "Album"
    assert page.created


def test_search_only_entities_and_errors() -> None:
    rec = Recorder(
        ok({"count": 1, "offset": 0, "tags": [{"name": "rock", "score": 100}]})
    )
    with make(rec) as mb:
        assert mb.tags.search("rock")[0].name == "rock"
        assert not hasattr(mb.tags, "get")
        with pytest.raises(ValidationError, match="not searchable"):
            mb.genres.search("rock")
        with pytest.raises(ValueError, match="empty search query"):
            mb.artists.search()


def test_query_helpers() -> None:
    assert escape("AC/DC") == "AC\\/DC"
    assert escape('a+b "c"') == 'a\\+b \\"c\\"'
    assert build_query(artist='Guns "N" Roses') == 'artist:"Guns \\"N\\" Roses"'
    assert (
        build_query(type=["album", "ep"], video=False, dur=1000)
        == '(type:"album" OR type:"ep") AND video:false AND dur:1000'
    )
    assert build_query("foo", country=None) == "foo"


# ------------------------------------------------------------------ non-MBID lookups


def test_discid_and_cdstub() -> None:
    rec = Recorder(
        ok(
            {
                "id": "x" * 27 + "-",
                "sectors": 100,
                "offsets": [150],
                "releases": [{"id": RELEASE, "title": "N"}],
            }
        ),
        ok(
            {
                "id": "y" * 27 + "-",
                "title": "Stub",
                "artist": "Someone",
                "track-count": 3,
            }
        ),
        ok({"release-count": 1, "release-offset": 0, "releases": [{"id": RELEASE}]}),
    )
    with make(rec) as mb:
        d = mb.lookup_discid("x" * 27 + "-", inc="artist-credits")
        assert isinstance(d, Disc)
        assert d.releases[0].title == "N"
        s = mb.lookup_discid("y" * 27 + "-", cdstubs=True)
        assert isinstance(s, CDStub)
        assert s.title == "Stub"
        f = mb.lookup_discid(
            "-", toc=[1, 2, 150, 20000, 150], cdstubs=False, all_media_formats=True
        )
        assert isinstance(f, Disc)
        assert len(f.releases) == 1
        with pytest.raises(ValidationError):
            mb.lookup_discid("short")
    assert rec.params(2) == {
        "toc": ["1+2+150+20000+150"],
        "cdstubs": ["no"],
        "media-format": ["all"],
        "fmt": ["json"],
    }


def test_isrc_iswc_and_normalisation() -> None:
    rec = Recorder(
        ok({"isrc": "USGF19942501", "recordings": [{"id": ARTIST, "title": "SLTS"}]}),
        ok(
            {
                "work-count": 1,
                "work-offset": 0,
                "works": [{"id": ARTIST, "title": "SLTS"}],
            }
        ),
    )
    with make(rec) as mb:
        assert mb.lookup_isrc("us-gf1-99-42501").recordings[0].title == "SLTS"
        assert mb.lookup_iswc("T0702436916")[0].title == "SLTS"
    assert rec.requests[0].url.path.endswith("/isrc/USGF19942501")
    assert rec.requests[1].url.path.endswith("/iswc/T-070.243.691-6")
    assert normalize_iswc("T-070.243.691-6") == "T-070.243.691-6"
    with pytest.raises(ValidationError):
        normalize_isrc("nope")


def test_url_by_resources() -> None:
    url = "https://example.com/a?x=1&y=%20"
    rec = Recorder(
        ok({"id": ARTIST, "resource": url}),
        ok(
            {"url-count": 1, "url-offset": 0, "urls": [{"id": ARTIST, "resource": url}]}
        ),
        httpx2.Response(codes.NOT_FOUND, json={"error": "Not Found"}),
    )
    with make(rec) as mb:
        assert mb.urls.by_resource(url, inc="artist-rels").resource == url
        assert len(mb.urls.by_resources([url, "https://other"])) == 1
        assert mb.urls.by_resources(["https://unknown"]) == []
    assert rec.params(0)["resource"] == [
        url
    ]  # encoded once on the wire, decoded back to the original


def test_genres() -> None:
    rec = Recorder(
        ok(
            {
                "genre-count": 2,
                "genre-offset": 0,
                "genres": [{"id": "g", "name": "rock"}],
            }
        ),
        httpx2.Response(codes.OK, text="rock\npop\n"),
    )
    with make(rec) as mb:
        assert mb.genres.all(limit=1).count == 2
        assert mb.genres.names() == ["rock", "pop"]
    assert rec.requests[1].url.path.endswith("/genre/all")
    assert rec.params(1)["fmt"] == ["txt"]


# ------------------------------------------------------------------ errors / retries


def test_error_mapping() -> None:
    rec = Recorder(
        httpx2.Response(
            codes.NOT_FOUND, json={"error": "Not Found", "help": "see docs"}
        ),
        httpx2.Response(
            codes.BAD_REQUEST,
            text="<?xml version='1.0'?>"
            "<error><text>Invalid inc</text><text>help!</text></error>",
        ),
    )
    with make(rec) as mb:
        with pytest.raises(NotFoundError) as e:
            mb.artists.get(ARTIST)
        assert e.value.status_code == codes.NOT_FOUND
        assert e.value.help == "see docs"
        with pytest.raises(BadRequestError, match="Invalid inc"):
            mb.artists.get(ARTIST)


def test_retries_on_503_then_succeeds() -> None:
    rec = Recorder(
        httpx2.Response(codes.SERVICE_UNAVAILABLE, headers={"Retry-After": "0"}),
        ok({"id": ARTIST, "name": "x"}),
    )
    with make(rec) as mb:
        assert mb.artists.get(ARTIST).name == "x"
    assert len(rec.requests) == 2


def test_retries_exhausted() -> None:
    rec = Recorder(httpx2.Response(codes.SERVICE_UNAVAILABLE))
    with make(rec, max_retries=2) as mb, pytest.raises(RateLimitError):
        mb.artists.get(ARTIST)
    assert len(rec.requests) == 3


def test_network_errors() -> None:
    rec = Recorder(httpx2.ReadTimeout("slow"), ok({"id": ARTIST}))
    with make(rec) as mb:
        assert mb.artists.get(ARTIST).id == ARTIST
    rec = Recorder(httpx2.ConnectError("down"))
    with make(rec, max_retries=1) as mb, pytest.raises(NetworkError):
        mb.artists.get(ARTIST)


def test_post_not_retried_after_read_error() -> None:
    rec = Recorder(
        httpx2.ReadTimeout("slow"), httpx2.Response(codes.OK, text="<metadata/>")
    )
    with make(rec, oauth_token="tok") as mb, pytest.raises(NetworkError):
        mb.submit_ratings("artist", {ARTIST: 80})
    assert len(rec.requests) == 1


def test_rate_limiter_spacing() -> None:
    import time

    rec = Recorder(ok({"id": ARTIST}))
    with make(rec, rate_limit=20) as mb:
        start = time.monotonic()
        for _ in range(4):
            mb.artists.get(ARTIST)
        assert time.monotonic() - start >= 0.14


# ------------------------------------------------------- submissions & collections


def _xml(req: httpx2.Request) -> ET.Element:
    return ET.fromstring(req.content)


def _find(element: ET.Element, path: str) -> ET.Element:
    return present(element.find(path))


NS = "{http://musicbrainz.org/ns/mmd-2.0#}"


def test_submissions_require_auth() -> None:
    with make(Recorder(ok({}))) as mb, pytest.raises(AuthenticationRequiredError):
        mb.submit_tags("artist", {ARTIST: ["rock"]})


def test_submit_tags_xml() -> None:
    rec = Recorder(
        httpx2.Response(
            codes.OK, text="<metadata><message><text>OK</text></message></metadata>"
        )
    )
    with make(rec, username="me", password="token") as mb:
        mb.submit_tags("release-group", {RELEASE: ["rock", "grunge"]}, vote="upvote")
    req = rec.last
    assert req.method == "POST"
    assert req.url.path.endswith("/tag")
    assert rec.params() == {"client": ["TestApp-1.0"]}
    assert req.headers["Content-Type"] == "application/xml; charset=utf-8"
    tags = _xml(req).findall(
        f"{NS}release-group-list/{NS}release-group/{NS}user-tag-list/{NS}user-tag"
    )
    assert [(t.get("vote"), _find(t, f"{NS}name").text) for t in tags] == [
        ("upvote", "rock"),
        ("upvote", "grunge"),
    ]


def test_submit_ratings_barcodes_isrcs() -> None:
    rec = Recorder(httpx2.Response(codes.OK, text="<metadata/>"))
    with make(rec, oauth_token="tok") as mb:
        mb.submit_ratings("recording", {ARTIST: 60})
        mb.submit_barcodes({RELEASE: "4006381333931"})
        mb.submit_isrcs({ARTIST: ["USGF19942501"]})
        with pytest.raises(ValidationError, match="checksum"):
            mb.submit_barcodes({RELEASE: "4006381333932"})
        with pytest.raises(ValidationError):
            mb.submit_ratings("release", {RELEASE: 60})
    assert rec.requests[0].headers["Authorization"] == "Bearer tok"
    assert _find(_xml(rec.requests[0]), f".//{NS}user-rating").text == "60"
    assert rec.requests[1].url.path.endswith("/release")
    assert _find(_xml(rec.requests[1]), f".//{NS}barcode").text == "4006381333931"
    isrc_list = _find(_xml(rec.requests[2]), f".//{NS}isrc-list")
    assert isrc_list.get("count") == "1"
    assert isrc_list[0].get("id") == "USGF19942501"


def test_submission_needs_hyphenless_version() -> None:
    mb = MusicBrainz(
        "App",
        "1.0-beta",
        oauth_token="t",
        transport=httpx2.MockTransport(Recorder(ok({}))),
    )
    with pytest.raises(ValidationError, match="client ID"):
        mb.submit_ratings("artist", {ARTIST: 20})


def test_collection_add_batches() -> None:
    rec = Recorder(httpx2.Response(codes.OK, text="<metadata/>"))
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(401)]
    with make(rec, oauth_token="t") as mb:
        mb.collections.add(COLL, "release", ids)
        mb.collections.remove(COLL, "release-groups", ids[:1])
    assert [r.method for r in rec.requests] == ["PUT", "PUT", "DELETE"]
    first = unquote(rec.requests[0].url.path)
    assert first.startswith(f"/ws/2/collection/{COLL}/releases/")
    assert first.count(";") == 399
    assert "/release-groups/" in rec.requests[2].url.path


def test_collection_reads() -> None:
    rec = Recorder(
        ok(
            {
                "collection-count": 1,
                "collection-offset": 0,
                "collections": [
                    {
                        "id": COLL,
                        "name": "Mine",
                        "entity-type": "release",
                        "release-count": 7,
                    }
                ],
            }
        ),
        ok({"release-count": 0, "release-offset": 0, "releases": []}),
    )
    with make(rec, oauth_token="t") as mb:
        c = mb.collections.of_editor("someone", include_private=True)[0]
        assert c.item_count == 7
        assert c.entity_type == "release"
        mb.collections.items(COLL, "release", inc="media")
    assert rec.params(0)["editor"] == ["someone"]
    assert rec.params(0)["inc"] == ["user-collections"]
    assert rec.params(1)["collection"] == [COLL]


def test_gtin() -> None:
    for good in ("4006381333931", "036000291452", "96385074"):
        assert check_gtin(good) == good


def test_raw_request() -> None:
    rec = Recorder(ok({"anything": True}))
    with make(rec) as mb:
        assert mb.request("GET", "/artist", {"query": "x"}) == {"anything": True}


# ------------------------------------------------------------------ async


def test_async_client() -> None:
    async def main() -> None:
        rec = Recorder(
            ok({"count": 2, "offset": 0, "artists": [{"id": ARTIST, "name": "a"}]}),
            ok({"count": 2, "offset": 1, "artists": [{"id": ARTIST, "name": "b"}]}),
            ok({"id": ARTIST, "name": "c"}),
        )
        async with AsyncMusicBrainz(
            "A", "1", transport=httpx2.MockTransport(rec), rate_limit=None
        ) as mb:
            names = [a.name async for a in mb.artists.iter_search(artist="x")]
            assert names == ["a", "b"]
            assert (await mb.artists.get(ARTIST)).name == "c"
            with pytest.raises(ValidationError):
                await mb.artists.get("bad")

    anyio.run(main)


def test_includes_split_inside_lists() -> None:
    rec = Recorder(ok({"id": ARTIST}))
    with make(rec) as mb:
        mb.artists.get(ARTIST, inc=["aliases", "url-rels,tags"])
        mb.artists.get(ARTIST, inc="aliases+url-rels tags")
    assert rec.params(0)["inc"] == rec.params(1)["inc"] == ["aliases+url-rels+tags"]


BILLION_LAUGHS = (
    '<?xml version="1.0"?><!DOCTYPE error [<!ENTITY a "aaaaaaaaaa">'
    + "".join(f'<!ENTITY {chr(98 + i)} "{f"&{chr(97 + i)};" * 10}">' for i in range(8))
    + "]><error><text>&i;</text></error>"
)
XXE = (
    '<?xml version="1.0"?><!DOCTYPE error [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    "<error><text>&x;</text></error>"
)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<error><text>Invalid inc</text><text>help</text></error>",
         ["Invalid inc", "help"]),
        ('<?xml version="1.0" encoding="UTF-8"?>'
         "<error><text> a &amp; b </text></error>",
         ["a & b"]),
        ('<error xmlns="http://musicbrainz.org/ns/mmd-2.0#"><text lang="en">ns</text>'
         "<context>not a message</context></error>",
         ["ns"]),
        ("<error><text><![CDATA[<raw>]]></text></error>", ["<raw>"]),
        ("<error><text>unclosed</error>", []),
        (BILLION_LAUGHS, []),
        (XXE, []),
        ("<!doctype error><error><text>x</text></error>", []),
    ],
)  # fmt: skip
def test_xml_error_texts(body: str, expected: list[str]) -> None:
    assert xml_error_texts(body) == expected


@pytest.mark.parametrize("body", [BILLION_LAUGHS, XXE])
def test_hostile_xml_error_body_falls_back_to_reason(body: str) -> None:
    rec = Recorder(httpx2.Response(codes.BAD_REQUEST, text=body))
    with make(rec) as mb, pytest.raises(BadRequestError) as e:
        mb.artists.get(ARTIST)
    assert e.value.message == "Bad Request"
