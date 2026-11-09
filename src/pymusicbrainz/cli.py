r"""Command-line interface (Typer) printing JSON.

Examples::

    pymusicbrainz search artist "Daft Punk" --limit 3
    pymusicbrainz lookup artist 056e4f3e-d505-4dad-8ec1-d04f521cbb56 -i aliases
    pymusicbrainz browse release-group --type album --all \
        --artist 056e4f3e-d505-4dad-8ec1-d04f521cbb56
    pymusicbrainz isrc GBAYE0601498
    pymusicbrainz url https://www.discogs.com/artist/1289
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.logging import RichHandler
from typer.models import OptionInfo

from . import _constants as consts
from ._constants import Entity, SearchEntity
from ._core import JSON, CoverArtSize
from ._version import __version__
from .client import MusicBrainz
from .errors import MusicBrainzError
from .models import Page

app = typer.Typer(
    name="pymusicbrainz",
    help="Query the MusicBrainz API. Results are printed as JSON.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
_stderr = Console(stderr=True)


@dataclass(slots=True)
class _Settings:
    app: str = "pymusicbrainz-cli"
    contact: str | None = None
    base_url: str = consts.DEFAULT_BASE_URL


# ------------------------------------------------------------------ helpers


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


def _emit(result: object) -> None:
    data = _dump(result)
    if sys.stdout.isatty():
        Console().print_json(data=data, ensure_ascii=False)
    else:  # plain, unwrapped JSON for pipes (jq etc.)
        json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")


def _run(ctx: typer.Context, action: Callable[[MusicBrainz], object]) -> None:
    """Run ``action`` with a client and print its result as JSON.

    Raises:
        typer.Exit: With status 1 after printing an API or validation error
            to stderr.
    """
    settings = ctx.ensure_object(_Settings)
    try:
        with MusicBrainz(
            settings.app, __version__, settings.contact, base_url=settings.base_url
        ) as mb:
            result = action(mb)
    except MusicBrainzError as exc:
        _stderr.print(f"[bold red]error:[/] {exc}", highlight=False)
        raise typer.Exit(1) from exc
    _emit(result)


_CLI_HANDLER = "_pymusicbrainz_cli"


def _setup_logging(verbosity: int) -> None:
    """Send the library's log records to stderr (stdout stays pure JSON).

    ``-v`` shows warnings (retries), ``-vv`` also shows every request.
    """
    log = logging.getLogger("pymusicbrainz")
    for handler in [h for h in log.handlers if getattr(h, _CLI_HANDLER, False)]:
        log.removeHandler(handler)  # repeated invocations in one process
    if verbosity <= 0:
        log.setLevel(logging.NOTSET)
        return
    handler: logging.Handler
    if sys.stderr.isatty():
        handler = RichHandler(
            console=Console(stderr=True), show_path=False, markup=False
        )
    else:  # one unwrapped line per record, so log files stay greppable
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    setattr(handler, _CLI_HANDLER, True)
    log.addHandler(handler)
    log.setLevel(logging.WARNING if verbosity == 1 else logging.DEBUG)


def _show_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


# ------------------------------------------------------------------ options

Inc = Annotated[
    list[str] | None,
    typer.Option(
        "--inc",
        "-i",
        help="Include; repeat or comma-separate: [cyan]-i aliases,url-rels[/]",
    ),
]
ReleaseType = Annotated[
    str | None,
    typer.Option("--type", help="Release (group) type filter, e.g. album or album|ep"),
]
ReleaseStatus = Annotated[
    str | None,
    typer.Option("--status", help="Release status filter, e.g. official"),
]
Limit = Annotated[int | None, typer.Option(min=1, max=consts.MAX_LIMIT)]
Offset = Annotated[int | None, typer.Option(min=0)]


def _link(name: str) -> OptionInfo:
    metavars = {"editor": "NAME", "resource": "URL"}
    return typer.Option(
        f"--{name}",
        metavar=metavars.get(name, "MBID"),
        help=f"Browse by {name}",
        rich_help_panel="Linked entity (exactly one)",
    )


# ------------------------------------------------------------------ commands


@app.callback()
def _main(
    ctx: typer.Context,
    app_name: Annotated[
        str,
        typer.Option(
            "--app", envvar="MUSICBRAINZ_APP", help="Application name for User-Agent"
        ),
    ] = "pymusicbrainz-cli",
    contact: Annotated[
        str | None,
        typer.Option(
            envvar="MUSICBRAINZ_CONTACT", help="Contact URL/e-mail for User-Agent"
        ),
    ] = None,
    base_url: Annotated[
        str, typer.Option(envvar="MUSICBRAINZ_URL", help="API root, e.g. a mirror")
    ] = consts.DEFAULT_BASE_URL,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Log to stderr: -v retries/warnings, -vv every request.",
        ),
    ] = 0,
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_show_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    _setup_logging(verbose)
    # Detach the handler when the command ends: it holds on to this run's
    # stderr, which a caller running the app in-process (tests, CliRunner)
    # closes afterwards.
    ctx.call_on_close(lambda: _setup_logging(0))
    ctx.obj = _Settings(app=app_name, contact=contact, base_url=base_url)


@app.command()
def lookup(
    ctx: typer.Context,
    entity: Annotated[Entity, typer.Argument(help="Entity type")],
    mbid: Annotated[str, typer.Argument(help="MBID of the entity")],
    *,
    inc: Inc = None,
    release_type: ReleaseType = None,
    release_status: ReleaseStatus = None,
) -> None:
    """Look up an entity by MBID."""
    _run(
        ctx,
        lambda mb: mb.resource(entity).get(
            mbid, inc=inc, release_type=release_type, release_status=release_status
        ),
    )


@app.command()
def search(
    ctx: typer.Context,
    entity: Annotated[SearchEntity, typer.Argument(help="Index to search")],
    query: Annotated[str, typer.Argument(help="Lucene query")],
    *,
    limit: Annotated[int, typer.Option(min=1, max=consts.MAX_LIMIT)] = 10,
    offset: Offset = None,
    dismax: Annotated[
        bool, typer.Option(help="Use the dismax parser (escapes special chars)")
    ] = False,
) -> None:
    """Search an entity index (Lucene syntax)."""
    attr = consts.PLURALS[entity].replace("-", "_")
    _run(
        ctx,
        lambda mb: getattr(mb, attr).search(
            query, limit=limit, offset=offset, dismax=dismax
        ),
    )


@app.command()
def browse(
    ctx: typer.Context,
    entity: Annotated[Entity, typer.Argument(help="Entity type to list")],
    *,
    area: Annotated[str | None, _link("area")] = None,
    artist: Annotated[str | None, _link("artist")] = None,
    collection: Annotated[str | None, _link("collection")] = None,
    editor: Annotated[str | None, _link("editor")] = None,
    event: Annotated[str | None, _link("event")] = None,
    label: Annotated[str | None, _link("label")] = None,
    place: Annotated[str | None, _link("place")] = None,
    recording: Annotated[str | None, _link("recording")] = None,
    release: Annotated[str | None, _link("release")] = None,
    release_group: Annotated[str | None, _link("release-group")] = None,
    resource: Annotated[str | None, _link("resource")] = None,
    track: Annotated[str | None, _link("track")] = None,
    track_artist: Annotated[str | None, _link("track_artist")] = None,
    work: Annotated[str | None, _link("work")] = None,
    inc: Inc = None,
    release_type: ReleaseType = None,
    release_status: ReleaseStatus = None,
    limit: Limit = None,
    offset: Offset = None,
    fetch_all: Annotated[
        bool, typer.Option("--all", help="Fetch every page (ignores --limit/--offset)")
    ] = False,
) -> None:
    """Browse entities linked to another entity."""
    links = {
        "area": area,
        "artist": artist,
        "collection": collection,
        "editor": editor,
        "event": event,
        "label": label,
        "place": place,
        "recording": recording,
        "release": release,
        "release_group": release_group,
        "resource": resource,
        "track": track,
        "track_artist": track_artist,
        "work": work,
    }
    # The linked entity is a dynamic keyword (artist=..., label=...), so the
    # arguments are assembled as a plain dict.
    kwargs: dict[str, Any] = {k: v for k, v in links.items() if v is not None}
    kwargs |= {
        "inc": inc,
        "release_type": release_type,
        "release_status": release_status,
    }

    def action(mb: MusicBrainz) -> object:
        res = mb.resource(entity)
        if fetch_all:
            return list(res.iter_browse(**kwargs))
        return res.browse(limit=limit, offset=offset, **kwargs)

    _run(ctx, action)


@app.command()
def isrc(
    ctx: typer.Context,
    code: Annotated[str, typer.Argument(help="ISRC, e.g. USGF19942501")],
    inc: Inc = None,
) -> None:
    """Recordings with an ISRC."""
    _run(ctx, lambda mb: mb.lookup_isrc(code, inc=inc))


@app.command()
def iswc(
    ctx: typer.Context,
    code: Annotated[str, typer.Argument(help="ISWC, e.g. T-070.243.691-6")],
    inc: Inc = None,
) -> None:
    """Works with an ISWC."""
    _run(ctx, lambda mb: mb.lookup_iswc(code, inc=inc))


@app.command()
def discid(
    ctx: typer.Context,
    disc_id: Annotated[
        str, typer.Argument(metavar="DISCID", help="Disc ID, or - with --toc")
    ],
    *,
    toc: Annotated[
        str | None,
        typer.Option(help="TOC for fuzzy matching, e.g. '1 12 267257 150 ...'"),
    ] = None,
    inc: Inc = None,
    cdstubs: Annotated[bool, typer.Option(help="Return CD stubs")] = True,
    all_formats: Annotated[
        bool, typer.Option("--all-formats", help="Fuzzy-match all media formats")
    ] = False,
) -> None:
    """Releases (or a CD stub) for a disc ID."""
    _run(
        ctx,
        lambda mb: mb.lookup_discid(
            disc_id,
            toc=toc,
            inc=inc,
            cdstubs=cdstubs,
            all_media_formats=all_formats,
        ),
    )


@app.command()
def url(
    ctx: typer.Context,
    urls: Annotated[list[str], typer.Argument(help="One or more URLs")],
    inc: Inc = None,
) -> None:
    """Look up URL entities by URL text (unknown URLs are skipped)."""
    _run(ctx, lambda mb: mb.urls.by_resources(urls, inc=inc))


@app.command("cover-art")
def cover_art(
    ctx: typer.Context,
    mbid: Annotated[str, typer.Argument(help="Release MBID (or release group)")],
    *,
    release_group: Annotated[
        bool, typer.Option("--release-group", help="MBID is a release group")
    ] = False,
    download: Annotated[
        Path | None,
        typer.Option(
            "--download", "-o", help="Save an image to this file instead of listing"
        ),
    ] = None,
    image: Annotated[
        str, typer.Option(help="front, back, or an image ID (with --download)")
    ] = "front",
    size: Annotated[
        int | None, typer.Option(help="Thumbnail size: 250, 500 or 1200")
    ] = None,
) -> None:
    """List a release's cover art, or download one image."""

    def action(mb: MusicBrainz) -> object:
        if download is None:
            if release_group:
                return mb.cover_art.release_group(mbid)
            return mb.cover_art.release(mbid)
        img = mb.cover_art.image(
            mbid,
            image,
            size=cast("CoverArtSize | None", size),  # validated by the core
            release_group=release_group,
        )
        download.write_bytes(img.data)
        return {
            "path": str(download),
            "bytes": len(img.data),
            "content_type": img.content_type,
            "url": img.url,
        }

    _run(ctx, action)


@app.command()
def genres(ctx: typer.Context) -> None:
    """List all genre names."""
    _run(ctx, lambda mb: mb.genres.names())


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point."""
    app(args=argv, prog_name="pymusicbrainz")


if __name__ == "__main__":
    main()
