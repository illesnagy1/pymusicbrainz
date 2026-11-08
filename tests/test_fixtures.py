"""Replay real API responses (recorded by ``scripts/record_fixtures.py``).

Each fixture is parsed exactly as the client would parse it, and the result must
round-trip back to the original JSON: a field the models mistype or drop fails.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from pymusicbrainz import CoverArt, Disc, Page
from pymusicbrainz._core import Core

FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST: dict[str, dict[str, Any]] = json.loads(
    (FIXTURES / "manifest.json").read_text()
)
core = Core()


def _dump(value: object) -> object:
    """Serialise a parse result back to API-shaped JSON."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_unset=True)
    if isinstance(value, Page):
        return [_dump(item) for item in value.items]
    if isinstance(value, list):
        return [_dump(item) for item in value]
    return value


def _expected(name: str, raw: Any) -> object:  # noqa: ANN401
    """The part of the raw JSON that the parse result corresponds to."""
    if isinstance(_parse(name, raw), Page):
        return next(v for k, v in raw.items() if isinstance(v, list))
    if MANIFEST[name]["method"] == "lookup_iswc":
        return raw["works"]
    if MANIFEST[name]["method"] == "lookup_urls":
        return raw.get("urls", [raw])
    return raw


def _parse(name: str, raw: object) -> object:
    spec = MANIFEST[name]
    call = getattr(core, spec["method"])(*spec["args"], **spec["kwargs"])
    return call.parse(raw)


def _known_coercions(data: object) -> object:
    """Apply the deliberate type normalisations the models make to raw JSON.

    The API is inconsistent in two places, and the models pick one type:

    * Cover Art Archive image ``id``: a number in practice, text in the docs.
    * Place ``coordinates``: numbers in lookups, strings in search results.
    """
    if isinstance(data, dict):
        out: dict[str, object] = {}
        for key, value in data.items():
            if key == "id" and isinstance(value, int):
                out[key] = str(value)
            elif key in ("latitude", "longitude") and isinstance(value, str):
                out[key] = float(value)
            else:
                out[key] = _known_coercions(value)
        return out
    if isinstance(data, list):
        return [_known_coercions(v) for v in data]
    return data


def test_manifest_matches_files() -> None:
    files = {p.stem for p in FIXTURES.glob("*.json")} - {"manifest"}
    assert files == set(MANIFEST)


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_fixture_round_trips(name: str) -> None:
    raw = json.loads((FIXTURES / f"{name}.json").read_text())
    expected = _known_coercions(_expected(name, raw))
    assert _dump(_parse(name, raw)) == expected


def test_typed_access_on_real_data() -> None:
    """Spot-check that real payloads land in typed fields, not model_extra."""

    def load(name: str) -> Any:  # noqa: ANN401
        return _parse(name, json.loads((FIXTURES / f"{name}.json").read_text()))

    artist = load("artist")
    assert artist.name == "Nirvana"
    assert artist.life_span.ended is True
    assert artist.relations_of("url")
    assert artist.rating.votes_count > 0

    release = load("release")
    assert release.title == "Nevermind"
    assert release.artist_credit_phrase == "Nirvana"
    assert release.media[0].tracks[0].recording.title
    assert release.label_info[0].label.name

    disc = load("discid")
    assert isinstance(disc, Disc)
    assert disc.releases

    art = load("cover-art")
    assert isinstance(art, CoverArt)
    assert art.front is not None
    assert art.front.thumbnail(250)

    page = load("search-artist")
    assert page.count > 0
    assert page[0].score == 100
