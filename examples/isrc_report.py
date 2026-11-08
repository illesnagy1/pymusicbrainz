"""Look up several ISRCs concurrently with the async client.

The requests run as concurrent tasks, but the client's rate limiter still
spaces them about a second apart, so MusicBrainz is never flooded::

    uv run python examples/isrc_report.py USGF19942501 GBAYE0601498 USRC17607839
"""

from __future__ import annotations

import argparse
import os
import sys

import anyio

from musicbrainz import AsyncMusicBrainz, MusicBrainzError


async def lookup(mb: AsyncMusicBrainz, isrc: str, results: dict[str, str]) -> None:
    """Describe the recordings for one ISRC."""
    try:
        found = await mb.lookup_isrc(isrc, inc=["artist-credits"])
    except MusicBrainzError as exc:
        results[isrc] = f"error: {exc}"
        return
    titles = {f"{r.artist_credit_phrase} - {r.title}" for r in found.recordings}
    results[isrc] = "; ".join(sorted(titles)) or "no recordings"


async def run(isrcs: list[str]) -> None:
    """Look up all ISRCs and print a report in input order."""
    results: dict[str, str] = {}
    async with (
        AsyncMusicBrainz(
            "musicbrainz-examples", "1", os.environ.get("MUSICBRAINZ_CONTACT")
        ) as mb,
        anyio.create_task_group() as tg,
    ):
        for isrc in isrcs:
            tg.start_soon(lookup, mb, isrc, results)
    for isrc in isrcs:
        print(f"{isrc}  {results[isrc]}")


def main(argv: list[str] | None = None) -> int:
    """Run the example."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("isrcs", nargs="+", metavar="ISRC")
    args = parser.parse_args(argv)
    anyio.run(run, args.isrcs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
