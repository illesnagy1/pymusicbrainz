"""Replay real API responses (recorded by ``scripts/record_fixtures.py``).

Each fixture is parsed exactly as the client would parse it, and the result must
round-trip back to the original JSON: a field the models mistype or drop fails.
The comparison lives in ``scripts/roundtrip.py``, shared with the weekly live
check (``record_fixtures.py --check``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import roundtrip

from pymusicbrainz import CoverArt, Disc
from pymusicbrainz._core import Call, Core

FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST: dict[str, dict[str, Any]] = json.loads(
    (FIXTURES / "manifest.json").read_text()
)
core = Core()


def _call(name: str) -> Call[Any]:
    spec = MANIFEST[name]
    return getattr(core, spec["method"])(*spec["args"], **spec["kwargs"])


def _parse(name: str, raw: object) -> object:
    return roundtrip.parse(_call(name), raw)


def test_manifest_matches_files() -> None:
    files = {p.stem for p in FIXTURES.glob("*.json")} - {"manifest"}
    assert files == set(MANIFEST)


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_fixture_round_trips(name: str) -> None:
    raw = json.loads((FIXTURES / f"{name}.json").read_text())
    parsed = _parse(name, raw)
    want = roundtrip.expected(MANIFEST[name]["method"], parsed, raw)
    assert roundtrip.differences(want, roundtrip.dump(parsed)) == []
    assert roundtrip.dump(parsed) == want


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
