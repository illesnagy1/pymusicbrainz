"""Small command-line interface printing JSON.

    musicbrainz search artist "Daft Punk" --limit 3
    musicbrainz lookup artist 056e4f3e-d505-4dad-8ec1-d04f521cbb56 --inc aliases url-rels
    musicbrainz browse release-group --artist 056e4f3e-d505-4dad-8ec1-d04f521cbb56 --type album --all
    musicbrainz isrc GBAYE0601498
    musicbrainz url https://www.discogs.com/artist/1289
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from pydantic import BaseModel

from . import _constants as C
from ._version import __version__
from .client import MusicBrainz
from .errors import MusicBrainzError
from .models import Page


def _dump(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", exclude_defaults=True)
    if isinstance(obj, Page):
        return {"count": obj.count, "offset": obj.offset, "items": [_dump(i) for i in obj.items]}
    if isinstance(obj, list):
        return [_dump(i) for i in obj]
    return obj


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="musicbrainz", description="Query the MusicBrainz API (JSON output).")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--app", default=os.environ.get("MUSICBRAINZ_APP", "musicbrainz-cli"))
    p.add_argument("--contact", default=os.environ.get("MUSICBRAINZ_CONTACT"))
    p.add_argument("--base-url", default=os.environ.get("MUSICBRAINZ_URL", C.DEFAULT_BASE_URL))
    sub = p.add_subparsers(dest="command", required=True)

    lk = sub.add_parser("lookup", help="look up an entity by MBID")
    lk.add_argument("entity", choices=[*C.CORE_ENTITIES, "collection"])
    lk.add_argument("mbid")
    lk.add_argument("--inc", nargs="*")
    lk.add_argument("--type", dest="release_type")
    lk.add_argument("--status", dest="release_status")

    se = sub.add_parser("search", help="search an entity index (Lucene syntax)")
    se.add_argument("entity", choices=C.SEARCH_ENTITIES)
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=10)
    se.add_argument("--offset", type=int)
    se.add_argument("--dismax", action="store_true")

    br = sub.add_parser("browse", help="browse entities linked to another entity")
    br.add_argument("entity", choices=sorted(C.BROWSE_LINKS))
    for link in sorted(set().union(*C.BROWSE_LINKS.values())):
        br.add_argument(f"--{link}", dest=f"link_{link.replace('-', '_')}", metavar="ID")
    br.add_argument("--inc", nargs="*")
    br.add_argument("--type", dest="release_type")
    br.add_argument("--status", dest="release_status")
    br.add_argument("--limit", type=int)
    br.add_argument("--offset", type=int)
    br.add_argument("--all", action="store_true", help="fetch every page")

    for name, helptext in (("isrc", "recordings by ISRC"), ("iswc", "works by ISWC")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("code")
        sp.add_argument("--inc", nargs="*")

    dc = sub.add_parser("discid", help="releases by disc ID")
    dc.add_argument("discid")
    dc.add_argument("--toc")
    dc.add_argument("--inc", nargs="*")

    ur = sub.add_parser("url", help="look up URL entities by URL text")
    ur.add_argument("urls", nargs="+")
    ur.add_argument("--inc", nargs="*")

    sub.add_parser("genres", help="list all genre names")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with MusicBrainz(args.app, __version__, args.contact, base_url=args.base_url) as mb:
            result: Any
            match args.command:
                case "lookup":
                    res = mb._resource(args.entity)
                    result = res.get(args.mbid, inc=args.inc, release_type=args.release_type,
                                     release_status=args.release_status)
                case "search":
                    result = getattr(mb, C.PLURALS[args.entity].replace("-", "_")).search(
                        args.query, limit=args.limit, offset=args.offset, dismax=args.dismax)
                case "browse":
                    links = {k[5:]: v for k, v in vars(args).items() if k.startswith("link_") and v}
                    res = mb._resource(args.entity)
                    kwargs = dict(inc=args.inc, release_type=args.release_type, release_status=args.release_status,
                                  **links)
                    if args.all:
                        result = list(res.iter_browse(**kwargs))
                    else:
                        result = res.browse(limit=args.limit, offset=args.offset, **kwargs)
                case "isrc":
                    result = mb.lookup_isrc(args.code, inc=args.inc)
                case "iswc":
                    result = mb.lookup_iswc(args.code, inc=args.inc)
                case "discid":
                    result = mb.lookup_discid(args.discid, toc=args.toc, inc=args.inc)
                case "url":
                    result = mb.urls.by_resources(args.urls, inc=args.inc)
                case "genres":
                    result = mb.genres.names()
                case _:
                    raise AssertionError(args.command)
    except MusicBrainzError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    json.dump(_dump(result), sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
