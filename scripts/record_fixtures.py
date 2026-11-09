"""Record real API responses for the tests, or check the live API against them.

Every request is built with the library's own sans-IO ``Core``, so it is
exactly what the client sends.

Record (refresh ``tests/fixtures``, replayed by ``tests/test_fixtures.py``)::

    uv run python scripts/record_fixtures.py

Check the live API for compatibility (weekly in CI, ``api-check.yml``)::

    uv run python scripts/record_fixtures.py --check --report report.md

The check fetches the same responses fresh and fails when one no longer
survives the client's models (see ``roundtrip.py``) or a request is rejected.
New fields the models don't define yet are reported, but are not failures.
Both modes take about 40 seconds because of the rate limit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import roundtrip
from httpx2 import codes
from pydantic import ValidationError as ModelError

from pymusicbrainz import MusicBrainz, NetworkError, ResponseError
from pymusicbrainz._core import JSON, Call, Core, raise_for_response

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
CONTACT = "https://github.com/illesnagy1/pymusicbrainz"

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

Fetch = Callable[[Call[Any]], JSON]
Spec = tuple[str, list[Any], dict[str, Any]]


def build(spec: Spec) -> Call[Any]:
    """Build the request for a spec with the library's own ``Core``.

    Returns:
        The request description.
    """
    method, args, kwargs = spec
    return getattr(Core(), method)(*args, **kwargs)


def specs(fetched: dict[str, JSON]) -> Iterator[tuple[str, Spec]]:
    """Every request, in order; the follow-up lookups use earlier searches.

    Yields:
        ``(fixture name, spec)``; a follow-up is skipped when its search
        wasn't fetched.
    """
    yield from FIXED.items()
    for entity in FROM_SEARCH:
        found = fetched.get(f"search-{entity}")
        if isinstance(found, dict):
            plural = "series" if entity == "series" else f"{entity}s"
            hits = found.get(plural)
            if isinstance(hits, list) and hits and isinstance(hits[0], dict):
                spec: Spec = ("lookup", [entity, hits[0]["id"]], {"inc": "aliases"})
                yield entity, spec


class Fetcher:
    """Fetch raw JSON the way the client does (rate-limited, with retries)."""

    def __init__(self) -> None:
        """Open the MusicBrainz client and a Cover Art Archive HTTP client."""
        self.mb = MusicBrainz("pymusicbrainz-fixtures", "1", CONTACT, rate_limit=1)
        self.caa = httpx2.Client(
            follow_redirects=True,
            timeout=30,
            transport=httpx2.HTTPTransport(retries=3),
            headers={"User-Agent": self.mb.user_agent},
        )

    def __call__(self, call: Call[Any]) -> JSON:
        """Fetch one request.

        Returns:
            The decoded JSON body.

        Raises:
            NetworkError: If the Cover Art Archive can't be reached.
        """
        if not call.external:
            return self.mb.request(call.method, call.path, dict(call.params))
        try:  # Cover Art Archive: not a WS/2 request
            resp = self.caa.get(call.path)
        except httpx2.TransportError as exc:
            raise NetworkError(str(exc)) from exc
        raise_for_response(
            resp.status_code, resp.content, str(resp.url), resp.reason_phrase
        )
        return resp.json()

    def close(self) -> None:
        """Close both HTTP clients."""
        self.mb.close()
        self.caa.close()


# ------------------------------------------------------------------ record


def record(fetch: Fetch) -> None:
    """Fetch every response and write the fixtures and their manifest."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    fetched: dict[str, JSON] = {}
    manifest: dict[str, dict[str, Any]] = {}
    for name, spec in specs(fetched):
        fetched[name] = body = fetch(build(spec))
        (FIXTURES / f"{name}.json").write_text(
            json.dumps(body, indent=2, ensure_ascii=False) + "\n"
        )
        method, args, kwargs = spec
        manifest[name] = {"method": method, "args": args, "kwargs": kwargs}
        print(f"recorded {name}")
    (FIXTURES / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )


# ------------------------------------------------------------------ check


@dataclass
class Result:
    """What a compatibility check found."""

    checked: int = 0
    problems: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    new_fields: set[tuple[str, str, str]] = field(default_factory=set)

    @property
    def exit_code(self) -> int:
        """0 compatible, 1 incompatible, 2 the API couldn't be reached."""
        if self.problems:
            return 1
        return 2 if self.unreachable else 0


def recorded_extra_fields() -> set[tuple[str, str]]:
    """Fields the recorded fixtures already carry that the models don't define.

    Returns:
        ``(model name, JSON key)`` pairs.
    """
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    known: set[tuple[str, str]] = set()
    for name, spec in manifest.items():
        call = build((spec["method"], spec["args"], spec["kwargs"]))
        raw = json.loads((FIXTURES / f"{name}.json").read_text())
        known |= roundtrip.extra_fields(roundtrip.parse(call, raw))
    return known


def check(fetch: Fetch, known_extras: set[tuple[str, str]]) -> Result:
    """Fetch every response fresh and compare it with the client's models.

    Returns:
        The problems, unreachable requests and new fields found.
    """
    result = Result()
    fetched: dict[str, JSON] = {}
    for name, spec in specs(fetched):
        call = build(spec)
        try:
            fetched[name] = raw = fetch(call)
        except ResponseError as exc:
            # A 4xx on a request the client builds means the API changed what
            # it accepts; 5xx and rate limiting are the service, not us.
            client_error = codes.is_client_error(exc.status_code)
            if client_error and exc.status_code != codes.TOO_MANY_REQUESTS:
                result.problems.append(f"`{name}`: rejected with {exc}")
            else:
                result.unreachable.append(f"`{name}`: {exc}")
            continue
        except NetworkError as exc:
            result.unreachable.append(f"`{name}`: {exc}")
            continue
        result.checked += 1
        try:
            parsed = roundtrip.parse(call, raw)
        except ModelError as exc:
            first = str(exc).splitlines()[:3]
            result.problems.append(f"`{name}`: no longer parses: {' / '.join(first)}")
            continue
        want = roundtrip.expected(spec[0], parsed, raw)
        result.problems += [
            f"`{name}`: {diff}"
            for diff in roundtrip.differences(want, roundtrip.dump(parsed))
        ]
        result.new_fields |= {
            (model, key, name)
            for model, key in roundtrip.extra_fields(parsed) - known_extras
        }
    return result


def report(result: Result) -> str:
    """Render a check result as Markdown (issue body and job summary).

    Returns:
        The report.
    """
    today = dt.datetime.now(dt.UTC).date().isoformat()
    lines = [f"## MusicBrainz API compatibility check, {today}", ""]
    if result.problems:
        lines += [
            (
                f"**{len(result.problems)} incompatibilities** in "
                f"{result.checked} responses checked. The client's models or "
                "request rules need updating."
            ),
            "",
            "### Problems",
            "",
            *[f"- {p}" for p in result.problems],
        ]
    elif result.unreachable:
        lines += ["**Inconclusive:** some requests couldn't be completed."]
    else:
        lines += [f"**Compatible:** all {result.checked} responses round-trip."]
    if result.unreachable:
        lines += ["", "### Not reached", "", *[f"- {u}" for u in result.unreachable]]
    if result.new_fields:
        lines += [
            "",
            "### New fields (not failures)",
            "",
            "Kept in `model_extra`; worth modelling if useful:",
            "",
            *[
                f"- `{model}`: `{key}` (in `{name}`)"
                for model, key, name in sorted(result.new_fields)
            ],
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Record the fixtures, or check the live API with ``--check``.

    Returns:
        The process exit status: in check mode 0 compatible, 1 incompatible,
        2 the API couldn't be reached.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="check, don't record")
    parser.add_argument("--report", type=Path, help="write the check report here")
    args = parser.parse_args(argv)

    fetcher = Fetcher()
    try:
        if not args.check:
            record(fetcher)
            return 0
        result = check(fetcher, recorded_extra_fields())
    finally:
        fetcher.close()
    text = report(result)
    print(text)
    if args.report:
        args.report.write_text(text)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
