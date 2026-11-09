"""Check that API responses survive the client's models without losing data.

Shared by ``tests/test_fixtures.py`` (recorded responses) and
``record_fixtures.py --check`` (fresh responses, weekly in CI). A response is
compatible when parsing it the way the client does and dumping it back gives
the original JSON, apart from the deliberate normalisations below.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from pymusicbrainz import Page
from pymusicbrainz._core import Call


def parse(call: Call[Any], raw: Any) -> object:  # noqa: ANN401 - raw API JSON
    """Parse a response exactly as the client would.

    Returns:
        The model, page or list the client would return.
    """
    return call.parse(raw)


def dump(value: object) -> object:
    """Serialise a parse result back to API-shaped JSON.

    Returns:
        API-shaped JSON.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_unset=True)
    if isinstance(value, Page):
        return [dump(item) for item in value.items]
    if isinstance(value, list):
        return [dump(item) for item in value]
    return value


def expected(method: str, parsed: object, raw: Any) -> object:  # noqa: ANN401
    """The part of the raw JSON that a parse result corresponds to.

    Returns:
        The matching part of ``raw``, with the known normalisations applied.
    """
    if isinstance(parsed, Page):
        part = next(v for v in raw.values() if isinstance(v, list))
    elif method == "lookup_iswc":
        part = raw["works"]
    elif method == "lookup_urls":
        part = raw.get("urls", [raw])
    else:
        part = raw
    return known_coercions(part)


def known_coercions(data: object) -> object:
    """Apply the deliberate type normalisations the models make to raw JSON.

    The API is inconsistent in two places, and the models pick one type:

    * Cover Art Archive image ``id``: a number in practice, text in the docs.
    * Place ``coordinates``: numbers in lookups, strings in search results.

    Returns:
        ``data`` with those normalisations applied.
    """
    if isinstance(data, dict):
        out: dict[str, object] = {}
        for key, value in data.items():
            if key == "id" and isinstance(value, int):
                out[key] = str(value)
            elif key in ("latitude", "longitude") and isinstance(value, str):
                out[key] = float(value)
            else:
                out[key] = known_coercions(value)
        return out
    if isinstance(data, list):
        return [known_coercions(v) for v in data]
    return data


def differences(
    want: object, got: object, path: str = "", limit: int = 10
) -> list[str]:
    """Describe where a dumped parse result differs from the raw JSON.

    Returns:
        Up to ``limit`` human-readable differences, empty when they match.
    """
    found: list[str] = []
    _compare(want, got, path, found)
    return found[:limit]


def _compare(want: object, got: object, at: str, found: list[str]) -> None:
    if isinstance(want, dict) and isinstance(got, dict):
        _compare_dicts(want, got, at, found)
    elif isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            found.append(
                f"`{at or '(top level)'}` has {len(got)} items, not {len(want)}"
            )
        for i, (a, b) in enumerate(zip(want, got, strict=False)):
            _compare(a, b, f"{at}[{i}]", found)
    elif type(want) is not type(got) or want != got:
        found.append(f"`{at or '(top level)'}` is {got!r} instead of {want!r}")


def _compare_dicts(
    want: dict[str, object], got: dict[str, object], at: str, found: list[str]
) -> None:
    for key in sorted(want.keys() | got.keys()):
        if key not in got:
            found.append(f"`{at}.{key}` was dropped")
        elif key not in want:
            found.append(f"`{at}.{key}` appeared from nowhere")
        else:
            _compare(want[key], got[key], f"{at}.{key}", found)


def extra_fields(value: object) -> set[tuple[str, str]]:
    """Fields the API sent that the models don't define (kept in model_extra).

    Returns:
        ``(model name, JSON key)`` pairs.
    """
    found: set[tuple[str, str]] = set()

    def walk(v: object) -> None:
        if isinstance(v, BaseModel):
            found.update((type(v).__name__, k) for k in v.model_extra or {})
            for name in type(v).model_fields:
                walk(getattr(v, name))
        elif isinstance(v, Page):
            for item in v.items:
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(value)
    return found
