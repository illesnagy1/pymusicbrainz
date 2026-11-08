"""Helpers for building Lucene search queries.

```pycon
>>> escape("AC/DC")
'AC\\\\/DC'
>>> build_query(artist="Daft Punk", country="FR")
'artist:"Daft Punk" AND country:"FR"'

```
"""

import re
from collections.abc import Iterable, Mapping

_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def escape(value: str) -> str:
    """Escape Lucene special characters in ``value``."""
    return _SPECIAL.sub(r"\\\1", value)


def phrase(value: str) -> str:
    """Quote ``value`` as an exact phrase."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _term(field: str, value: object) -> str:
    if isinstance(value, bool):
        return f"{field}:{str(value).lower()}"
    if isinstance(value, (int, float)):
        return f"{field}:{value}"
    if isinstance(value, (list, tuple, set, frozenset)):
        inner = " OR ".join(_term(field, v) for v in value)
        return f"({inner})"
    return f"{field}:{phrase(str(value))}"


def build_query(
    query: str | None = None,
    fields: Mapping[str, object] | None = None,
    /,
    *,
    operator: str = "AND",
    **kwargs: object,
) -> str:
    """Combine a free-text query and ``field=value`` terms into one Lucene query.

    * strings are matched as exact phrases (``field:"value"``)
    * lists/tuples/sets become ``(field:a OR field:b)``
    * ``None`` values are ignored
    * field names with ``_`` are kept as-is (MusicBrainz uses e.g. ``release_count``)
    """
    terms = [
        _term(f, v) for f, v in {**(fields or {}), **kwargs}.items() if v is not None
    ]
    parts: list[str] = []
    if query:
        parts.append(f"({query})" if terms else query)
    parts += terms
    if not parts:
        raise ValueError("an empty search query is not allowed")
    return f" {operator} ".join(parts)


def any_of(values: Iterable[str]) -> str:
    """``(a OR b OR c)`` from phrases."""
    return "(" + " OR ".join(phrase(v) for v in values) + ")"
