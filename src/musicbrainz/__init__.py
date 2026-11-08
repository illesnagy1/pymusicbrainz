"""Python client for the MusicBrainz WS/2 API.

Quick start::

    from musicbrainz import MusicBrainz

    with MusicBrainz("MyApp", "1.0", "me@example.com") as mb:
        nirvana = mb.artists.search(artist="Nirvana", limit=1)[0]
        for rg in mb.release_groups.iter_browse(
            artist=nirvana.id, release_type="album"
        ):
            print(rg.first_release_date, rg.title)
"""

from . import models, query
from ._constants import (
    BROWSE_LINKS,
    CORE_ENTITIES,
    LOOKUP_INCLUDES,
    RELEASE_STATUSES,
    RELEASE_TYPES,
    SEARCH_ENTITIES,
)
from ._ratelimit import AsyncRateLimiter, RateLimiter
from ._version import __version__
from .client import AsyncMusicBrainz, MusicBrainz
from .errors import (
    AuthenticationError,
    AuthenticationRequiredError,
    BadRequestError,
    ForbiddenError,
    MusicBrainzError,
    NetworkError,
    NotFoundError,
    RateLimitError,
    ResponseError,
    ServerError,
    ValidationError,
)
from .models import (
    Alias,
    Annotation,
    Area,
    Artist,
    ArtistCredit,
    CDStub,
    Collection,
    Disc,
    Event,
    Genre,
    Instrument,
    IsrcResult,
    Label,
    LabelInfo,
    Medium,
    Page,
    Place,
    Rating,
    Recording,
    Relation,
    Release,
    ReleaseGroup,
    Series,
    Tag,
    Track,
    Url,
    Work,
)
from .query import build_query, escape

__all__ = [
    "BROWSE_LINKS",
    "CORE_ENTITIES",
    "LOOKUP_INCLUDES",
    "RELEASE_STATUSES",
    "RELEASE_TYPES",
    "SEARCH_ENTITIES",
    "Alias",
    "Annotation",
    "Area",
    "Artist",
    "ArtistCredit",
    "AsyncMusicBrainz",
    "AsyncRateLimiter",
    "AuthenticationError",
    "AuthenticationRequiredError",
    "BadRequestError",
    "CDStub",
    "Collection",
    "Disc",
    "Event",
    "ForbiddenError",
    "Genre",
    "Instrument",
    "IsrcResult",
    "Label",
    "LabelInfo",
    "Medium",
    "MusicBrainz",
    "MusicBrainzError",
    "NetworkError",
    "NotFoundError",
    "Page",
    "Place",
    "RateLimitError",
    "RateLimiter",
    "Rating",
    "Recording",
    "Relation",
    "Release",
    "ReleaseGroup",
    "ResponseError",
    "Series",
    "ServerError",
    "Tag",
    "Track",
    "Url",
    "ValidationError",
    "Work",
    "__version__",
    "build_query",
    "escape",
    "main",
    "models",
    "query",
]


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point (imports the CLI lazily)."""
    from .cli import main as _main

    _main(argv)
