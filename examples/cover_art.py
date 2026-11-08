"""Download the front cover of an album.

uv run python examples/cover_art.py "Nirvana" "Nevermind"
uv run python examples/cover_art.py "Daft Punk" "Discovery" --size 500 -o covers/
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from pathlib import Path

from musicbrainz import MusicBrainz, NotFoundError


def main(argv: list[str] | None = None) -> int:
    """Run the example."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artist")
    parser.add_argument("album")
    parser.add_argument("--size", type=int, choices=[250, 500, 1200])
    parser.add_argument("-o", "--output", type=Path, default=Path())
    args = parser.parse_args(argv)

    with MusicBrainz(
        "musicbrainz-examples", "1", os.environ.get("MUSICBRAINZ_CONTACT")
    ) as mb:
        # Search release groups (albums), not releases: a release group's
        # cover is the one the community picked among all its editions.
        hits = mb.release_groups.search(
            releasegroup=args.album, artist=args.artist, primarytype="album", limit=1
        )
        if not hits:
            print("No such album found", file=sys.stderr)
            return 1
        rg = hits[0]
        print(
            f"Found: {rg.artist_credit_phrase} - {rg.title} ({rg.first_release_date})"
        )

        try:
            image = mb.cover_art.front(rg.id, size=args.size, release_group=True)
        except NotFoundError:
            print("This album has no cover art yet.", file=sys.stderr)
            return 1

    extension = mimetypes.guess_extension(image.content_type) or ".jpg"
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / f"{rg.title}{extension}".replace("/", "-")
    target.write_bytes(image.data)
    print(f"Saved {len(image.data) // 1024} KiB to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
