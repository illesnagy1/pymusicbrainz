"""Identify the CD in your drive (or a disc ID you give it).

Reading the drive needs the ``discid`` package and the libdiscid C library
(``apt install libdiscid0``, ``brew install libdiscid``, ...)::

    uv run --with discid python examples/find_disc.py
    uv run python examples/find_disc.py --disc-id 2y0jFMwYQP7gb9iSUKagmoT6DIw-
"""

from __future__ import annotations

import argparse
import os
import sys

from pymusicbrainz import CDStub, MusicBrainz


def read_drive(device: str | None) -> tuple[str, str | None]:
    """Read the disc ID and table of contents from a CD drive."""
    try:
        import discid
    except (ImportError, OSError) as exc:
        sys.exit(f"Reading a CD needs the discid package and libdiscid: {exc}")
    disc = discid.read(device)
    return disc.id, disc.toc_string


def main(argv: list[str] | None = None) -> int:
    """Run the example."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--disc-id", help="look up this disc ID instead of a CD")
    parser.add_argument("--device", help="CD drive, e.g. /dev/cdrom")
    args = parser.parse_args(argv)

    disc_id, toc = (args.disc_id, None) if args.disc_id else read_drive(args.device)
    print(f"Disc ID: {disc_id}")

    with MusicBrainz(
        "pymusicbrainz-examples", "1", os.environ.get("MUSICBRAINZ_CONTACT")
    ) as mb:
        # With the TOC, MusicBrainz can also fuzzy-match discs whose exact ID
        # hasn't been submitted yet.
        result = mb.lookup_discid(disc_id, toc=toc, inc=["artist-credits", "labels"])

    if isinstance(result, CDStub):
        print(f"Only an unreviewed CD stub: {result.artist} - {result.title}")
        return 0
    if not result.releases:
        print("Unknown disc. It can be added to MusicBrainz.")
        return 1
    for release in result.releases:
        label = release.label_info[0] if release.label_info else None
        catno = label.catalog_number if label else None
        print(
            f"  {release.artist_credit_phrase} - {release.title}"
            f"  [{release.date or '?'} {release.country or ''} {catno or ''}]".rstrip()
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
