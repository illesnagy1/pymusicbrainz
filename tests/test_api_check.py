"""The live API compatibility check, run offline against the recorded fixtures."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import record_fixtures as rf

from pymusicbrainz import NetworkError
from pymusicbrainz._core import JSON, Call
from pymusicbrainz.errors import error_for_status

FIXTURES = Path(__file__).parent / "fixtures"


def recorded() -> dict[tuple[str, tuple[tuple[str, str], ...]], JSON]:
    """Every recorded response, keyed by the request that produced it.

    Returns:
        ``(path, params) -> body``.
    """
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    bodies = {}
    for name, spec in manifest.items():
        call = rf.build((spec["method"], spec["args"], spec["kwargs"]))
        key = (call.path, tuple(call.params))
        bodies[key] = json.loads((FIXTURES / f"{name}.json").read_text())
    return bodies


class FakeAPI:
    """Serves the recorded fixtures; tests break individual responses."""

    def __init__(self) -> None:
        self.bodies = recorded()
        self.errors: dict[str, Exception] = {}

    def __call__(self, call: Call[Any]) -> JSON:
        for path, exc in self.errors.items():
            # "label" matches that path exactly; "isrc/" any path under it.
            if call.path == path or (path.endswith("/") and path in call.path):
                raise exc
        return copy.deepcopy(self.bodies[call.path, tuple(call.params)])

    def body(self, path_fragment: str) -> Any:  # noqa: ANN401 - raw JSON
        """The recorded body whose request path contains ``path_fragment``.

        Returns:
            The (mutable) body.
        """
        return next(b for (p, _), b in self.bodies.items() if path_fragment in p)


@pytest.fixture
def api() -> FakeAPI:
    return FakeAPI()


NIRVANA_PATH = f"artist/{rf.NIRVANA}"


def test_recorded_responses_are_compatible(api: FakeAPI) -> None:
    result = rf.check(api, rf.recorded_extra_fields())
    assert result.problems == []
    assert result.unreachable == []
    assert result.new_fields == set()
    assert result.checked == len(json.loads((FIXTURES / "manifest.json").read_text()))
    assert result.exit_code == 0
    assert "**Compatible:**" in rf.report(result)


def test_type_change_is_incompatible(api: FakeAPI) -> None:
    api.body(NIRVANA_PATH)["life-span"] = "1987 to 1994"  # an object before
    result = rf.check(api, rf.recorded_extra_fields())
    assert result.exit_code == 1
    assert len(result.problems) == 1
    assert result.problems[0].startswith("`artist`: no longer parses")


def test_lossy_conversion_is_incompatible(api: FakeAPI) -> None:
    # A string where the model expects a number is coerced: the round trip
    # notices the value changed type.
    api.body(f"recording/{rf.SLTS}")["length"] = "301133"
    result = rf.check(api, rf.recorded_extra_fields())
    assert result.exit_code == 1
    assert result.problems == ["`recording`: `.length` is 301133 instead of '301133'"]


def test_rejected_request_is_incompatible(api: FakeAPI) -> None:
    api.errors["isrc/"] = error_for_status(400, "isrcs is not a valid option")
    result = rf.check(api, rf.recorded_extra_fields())
    assert result.exit_code == 1
    assert result.problems == [
        "`isrc`: rejected with HTTP 400: isrcs is not a valid option"
    ]
    text = rf.report(result)
    assert "**1 incompatibilities**" in text
    assert "### Problems" in text


@pytest.mark.parametrize(
    "exc",
    [error_for_status(503, "busy"), error_for_status(429, "slow down"),
     NetworkError("connection reset")],
)  # fmt: skip
def test_service_trouble_is_inconclusive(api: FakeAPI, exc: Exception) -> None:
    api.errors["discid/"] = exc
    result = rf.check(api, rf.recorded_extra_fields())
    assert result.problems == []
    assert len(result.unreachable) == 1
    assert result.exit_code == 2
    assert "**Inconclusive:**" in rf.report(result)


def test_new_fields_are_reported_not_failures(api: FakeAPI) -> None:
    api.body(NIRVANA_PATH)["brand-new-field"] = {"x": 1}
    result = rf.check(api, rf.recorded_extra_fields())
    assert result.exit_code == 0
    assert result.new_fields == {("Artist", "brand-new-field", "artist")}
    assert "`Artist`: `brand-new-field` (in `artist`)" in rf.report(result)


def test_follow_up_lookups_skipped_when_search_fails(api: FakeAPI) -> None:
    api.errors["label"] = error_for_status(503, "busy")  # the label search
    # Follow-up lookups only exist once their search was fetched:
    assert "label" not in [name for name, _ in rf.specs({})]
    result = rf.check(api, rf.recorded_extra_fields())
    assert result.unreachable == ["`search-label`: HTTP 503: busy"]
    assert result.checked == len(rf.FIXED) - 1 + len(rf.FROM_SEARCH) - 1


def test_main_check_and_record(
    api: FakeAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Fake:
        def __call__(self, call: Call[Any]) -> JSON:
            return api(call)

        def close(self) -> None:
            pass

    monkeypatch.setattr(rf, "Fetcher", Fake)
    report = tmp_path / "report.md"
    assert rf.main(["--check", "--report", str(report)]) == 0
    assert "**Compatible:**" in report.read_text()

    # Record mode writes fixtures and a manifest that replay identically.
    monkeypatch.setattr(rf, "FIXTURES", tmp_path / "fixtures")
    assert rf.main([]) == 0
    written = json.loads((tmp_path / "fixtures" / "manifest.json").read_text())
    assert written == json.loads((FIXTURES / "manifest.json").read_text())
