"""Record real API responses into ``tests/fixtures`` for the test suite.

Every request is built with the library's own sans-IO ``Core``, so the recorded
requests are exactly what the client sends. ``tests/test_fixtures.py`` replays
them through the client's parsers and checks that nothing is lost.

Run it occasionally to catch API changes (it takes about half a minute because
of the rate limit)::

    uv run python scripts/record_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx2

from pymusicbrainz import MusicBrainz
from pymusicbrainz._core import JSON, Core

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

NIRVANA = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"
NEVERMIND_RG = "1b022e01-4da6-387b-8658-8678046e4cef"
NEVERMIND = "0fb1bdd3-1443-4569-943f-fc69c6424f99"
SLTS = "5fb524f1-8cc8-4c04-a921-e34c0a911ea7"  # Smells Like Teen Spirit
DISCID = "2y0jFMwYQP7gb9iSUKagmoT6DIw-"

#: name -> (Core method, positional args, keyword args)
FIXED: dict[str, tuple[str, list[Any], dict[str, Any]]] = {
    "artist": (
        "lookup",
        ["artist", NIRVANA],
        {"inc": "aliases+tags+genres+ratings+url-rels+artist-rels+annotation"},
    ),
    "release": (
        "lookup",
        ["release", NEVERMIND],
        {
            "inc": "recordings+artist-credits+labels+release-groups+media+discids"
            "+isrcs+url-rels+tags"
        },
    ),
    "release-group": (
        "lookup",
        ["release-group", NEVERMIND_RG],
        {"inc": "releases+artist-credits+ratings+genres+aliases"},
    ),
    "recording": (
        "lookup",
        ["recording", SLTS],
        {"inc": "isrcs+work-rels+artist-credits+releases+artist-rels"},
    ),
    "search-artist": ("search", ["artist", None, {"artist": "Nirvana"}, 5], {}),
    "search-release": ("search", ["release", "nevermind", {"arid": NIRVANA}, 5], {}),
    "search-release-group": (
        "search",
        ["release-group", None, {"releasegroup": "Nevermind"}, 5],
        {},
    ),
    "search-recording": (
        "search",
        ["recording", "smells like teen spirit", None, 5],
        {},
    ),
    "search-work": ("search", ["work", "smells like teen spirit", None, 5], {}),
    "search-label": ("search", ["label", None, {"label": "Sub Pop"}, 3], {}),
    "search-area": ("search", ["area", None, {"area": "Seattle"}, 3], {}),
    "search-event": ("search", ["event", "festival", None, 3], {}),
    "search-place": ("search", ["place", "studio", None, 3], {}),
    "search-instrument": ("search", ["instrument", "guitar", None, 3], {}),
    "search-series": ("search", ["series", "award", None, 3], {}),
    "search-annotation": ("search", ["annotation", "nirvana", None, 3], {}),
    "search-cdstub": ("search", ["cdstub", "nirvana", None, 3], {}),
    "search-tag": ("search", ["tag", "grunge", None, 3], {}),
    "search-url": ("search", ["url", None, {"url": "nirvana"}, 3], {}),
    "browse-releases": (
        "browse",
        ["release", {"artist": NIRVANA}, "media+labels+artist-credits", 5],
        {},
    ),
    "browse-release-groups": (
        "browse",
        ["release-group", {"artist": NIRVANA}, None, 10],
        {"release_type": "album"},
    ),
    "browse-collections": ("browse", ["collection", {"editor": "rob"}, None, 5], {}),
    "genres": ("genres_all", [10], {}),
    "discid": ("lookup_discid", [DISCID], {}),
    "isrc": ("lookup_isrc", ["USGF19942501"], {}),
    "iswc": ("lookup_iswc", ["T-070.243.691-6"], {}),
    "url": ("lookup_urls", [["https://www.discogs.com/artist/125246"]], {}),
    "cover-art": ("cover_art_list", [NEVERMIND], {}),
}

#: Entities looked up by the first result of the matching search above.
FROM_SEARCH = ["label", "area", "event", "place", "instrument", "series"]


def main() -> int:
    """Record every fixture and write the manifest.

    Returns:
        The process exit status, 0.
    """
    core = Core()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, Any]] = {}
    with (
        MusicBrainz(
            "pymusicbrainz-fixtures", "1", "https://github.com/", rate_limit=1
        ) as mb,
        httpx2.Client(follow_redirects=True, timeout=30) as caa,
    ):

        def record(
            name: str, method: str, args: list[Any], kwargs: dict[str, Any]
        ) -> JSON:
            call = getattr(core, method)(*args, **kwargs)
            if call.external:  # Cover Art Archive: not a WS/2 request
                data = caa.get(call.path, headers={"User-Agent": mb.user_agent})
                data.raise_for_status()
                body = data.json()
            else:
                body = mb.request(call.method, call.path, dict(call.params))
            (FIXTURES / f"{name}.json").write_text(
                json.dumps(body, indent=2, ensure_ascii=False) + "\n"
            )
            manifest[name] = {"method": method, "args": args, "kwargs": kwargs}
            print(f"recorded {name}")
            return body

        for name, (method, args, kwargs) in FIXED.items():
            record(name, method, args, kwargs)
        for entity in FROM_SEARCH:
            found = json.loads((FIXTURES / f"search-{entity}.json").read_text())
            plural = "series" if entity == "series" else f"{entity}s"
            mbid = found[plural][0]["id"]
            record(entity, "lookup", [entity, mbid], {"inc": "aliases"})

    (FIXTURES / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
