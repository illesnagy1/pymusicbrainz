"""Print an artist's studio albums, oldest first.

uv run python examples/discography.py "Nirvana"
uv run python examples/discography.py "Björk" --type ep
"""

from __future__ import annotations

import argparse
import os
import sys

from pymusicbrainz import MusicBrainz


def main(argv: list[str] | None = None) -> int:
    """Run the example."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artist", help="artist name to search for")
    parser.add_argument("--type", default="album", help="album, ep, single, ...")
    args = parser.parse_args(argv)

    with MusicBrainz(
        "pymusicbrainz-examples", "1", os.environ.get("MUSICBRAINZ_CONTACT")
    ) as mb:
        # The best search match (results are sorted by score).
        hits = mb.artists.search(artist=args.artist, limit=1)
        if not hits:
            print(f"No artist found for {args.artist!r}", file=sys.stderr)
            return 1
        artist = hits[0]
        print(f"{artist.name} ({artist.disambiguation or artist.type or 'artist'})\n")

        # Browse returns every release group of the artist, page by page.
        # Secondary types (live, compilation, ...) are excluded to keep only
        # studio releases.
        groups = [
            rg
            for rg in mb.release_groups.iter_browse(
                artist=artist.id, release_type=args.type
            )
            if not rg.secondary_types
        ]
        for rg in sorted(groups, key=lambda rg: rg.first_release_date or "9999"):
            year = (rg.first_release_date or "????")[:4]
            print(f"  {year}  {rg.title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
