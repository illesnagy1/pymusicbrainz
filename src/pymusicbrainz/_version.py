"""The package version, read from the installed metadata.

``pyproject.toml`` is the only place the version is written: the release pull
request bumps it there with ``uv version``.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pymusicbrainz")
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled tree
    __version__ = "0+unknown"
