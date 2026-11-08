"""Small command-line interface printing JSON.

Examples::

    musicbrainz search artist "Daft Punk" --limit 3
    musicbrainz lookup artist 056e4f3e-d505-4dad-8ec1-d04f521cbb56 --inc aliases
    musicbrainz browse release-group --type album --all \\
        --artist 056e4f3e-d505-4dad-8ec1-d04f521cbb56
    musicbrainz isrc GBAYE0601498
    musicbrainz url https://www.discogs.com/artist/1289
"""

import argparse
import json
import os
import sys
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from . import _constants as consts
from ._core import JSON
from ._version import __version__
from .client import MusicBrainz
from .errors import MusicBrainzError
from .models import Page


def _dump(obj: object) -> JSON:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", exclude_defaults=True)
    if isinstance(obj, Page):
        return {
            "count": obj.count,
            "offset": obj.offset,
            "items": [_dump(i) for i in obj.items],
        }
    if isinstance(obj, list):
        return [_dump(i) for i in obj]
    if obj is None or isinstance(obj, (str, int, float, bool, dict)):
        return obj
    return str(obj)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="musicbrainz", description="Query the MusicBrainz API (JSON output)."
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument(
        "--app", default=os.environ.get("MUSICBRAINZ_APP", "musicbrainz-cli")
    )
    p.add_argument("--contact", default=os.environ.get("MUSICBRAINZ_CONTACT"))
    p.add_argument(
        "--base-url", default=os.environ.get("MUSICBRAINZ_URL", consts.DEFAULT_BASE_URL)
    )
    sub = p.add_subparsers(dest="command", required=True)

    lk = sub.add_parser("lookup", help="look up an entity by MBID")
    lk.add_argument("entity", choices=[*consts.CORE_ENTITIES, "collection"])
    lk.add_argument("mbid")
    lk.add_argument("--inc", nargs="*")
    lk.add_argument("--type", dest="release_type")
    lk.add_argument("--status", dest="release_status")

    se = sub.add_parser("search", help="search an entity index (Lucene syntax)")
    se.add_argument("entity", choices=consts.SEARCH_ENTITIES)
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=10)
    se.add_argument("--offset", type=int)
    se.add_argument("--dismax", action="store_true")

    br = sub.add_parser("browse", help="browse entities linked to another entity")
    br.add_argument("entity", choices=sorted(consts.BROWSE_LINKS))
    for link in sorted(set().union(*consts.BROWSE_LINKS.values())):
        br.add_argument(
            f"--{link}", dest=f"link_{link.replace('-', '_')}", metavar="ID"
        )
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


def _lookup(mb: MusicBrainz, args: argparse.Namespace) -> object:
    return mb.resource(args.entity).get(
        args.mbid,
        inc=args.inc,
        release_type=args.release_type,
        release_status=args.release_status,
    )


def _search(mb: MusicBrainz, args: argparse.Namespace) -> object:
    resource = getattr(mb, consts.PLURALS[args.entity].replace("-", "_"))
    return resource.search(
        args.query, limit=args.limit, offset=args.offset, dismax=args.dismax
    )


def _browse(mb: MusicBrainz, args: argparse.Namespace) -> object:
    # The linked entity is a dynamic keyword (artist=..., label=...), so the
    # arguments are assembled as a plain dict.
    kwargs: dict[str, Any] = {
        k.removeprefix("link_"): v
        for k, v in vars(args).items()
        if k.startswith("link_") and v
    }
    kwargs |= {
        "inc": args.inc,
        "release_type": args.release_type,
        "release_status": args.release_status,
    }
    resource = mb.resource(args.entity)
    if args.all:
        return list(resource.iter_browse(**kwargs))
    return resource.browse(limit=args.limit, offset=args.offset, **kwargs)


COMMANDS: dict[str, Callable[[MusicBrainz, argparse.Namespace], object]] = {
    "lookup": _lookup,
    "search": _search,
    "browse": _browse,
    "isrc": lambda mb, a: mb.lookup_isrc(a.code, inc=a.inc),
    "iswc": lambda mb, a: mb.lookup_iswc(a.code, inc=a.inc),
    "discid": lambda mb, a: mb.lookup_discid(a.discid, toc=a.toc, inc=a.inc),
    "url": lambda mb, a: mb.urls.by_resources(a.urls, inc=a.inc),
    "genres": lambda mb, _: mb.genres.names(),
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with MusicBrainz(
            args.app, __version__, args.contact, base_url=args.base_url
        ) as mb:
            result = COMMANDS[args.command](mb, args)
    except MusicBrainzError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    json.dump(_dump(result), sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
