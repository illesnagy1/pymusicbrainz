"""Export the releases in an editor's public collections to CSV.

    uv run python examples/export_collection.py some-editor > releases.csv

To include private collections, log in as that editor: set
``MUSICBRAINZ_USERNAME`` and ``MUSICBRAINZ_TOKEN`` (a token from
https://musicbrainz.org/account/applications) and pass ``--private``.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

from musicbrainz import MusicBrainz


def main(argv: list[str] | None = None) -> int:
    """Run the example."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("editor", help="MusicBrainz editor (user) name")
    parser.add_argument("--private", action="store_true", help="needs credentials")
    args = parser.parse_args(argv)

    username = os.environ.get("MUSICBRAINZ_USERNAME") if args.private else None
    with MusicBrainz(
        "musicbrainz-examples",
        "1",
        os.environ.get("MUSICBRAINZ_CONTACT"),
        username=username,
        password=os.environ.get("MUSICBRAINZ_TOKEN") if username else None,
    ) as mb:
        out = csv.writer(sys.stdout)
        out.writerow(["collection", "release_mbid", "artist", "title", "date"])
        collections = mb.collections.of_editor(
            args.editor, include_private=args.private, limit=100
        )
        for collection in collections:
            if collection.entity_type != "release":
                continue  # this example only exports release collections
            print(
                f"{collection.name}: {collection.item_count} releases", file=sys.stderr
            )
            for release in mb.collections.iter_items(
                collection.id, "release", inc="artist-credits"
            ):
                out.writerow(
                    [
                        collection.name,
                        release.id,
                        release.artist_credit_phrase,
                        release.title,
                        release.date or "",
                    ]
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
