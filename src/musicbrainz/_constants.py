"""Static knowledge about the MusicBrainz WS/2 API.

Entities, includes, browse links and filter values.
"""

from typing import Literal, get_args

DEFAULT_BASE_URL = "https://musicbrainz.org/ws/2/"
MMD_NAMESPACE = "http://musicbrainz.org/ns/mmd-2.0#"

#: Core entities: lookups by MBID, browsing (except genre/url quirks below).
CoreEntity = Literal[
    "area",
    "artist",
    "event",
    "genre",
    "instrument",
    "label",
    "place",
    "recording",
    "release",
    "release-group",
    "series",
    "work",
    "url",
]
#: Everything that can be looked up by MBID or browsed.
Entity = Literal[CoreEntity, "collection"]
#: Entities with a search index.
SearchEntity = Literal[
    "annotation",
    "area",
    "artist",
    "cdstub",
    "event",
    "instrument",
    "label",
    "place",
    "recording",
    "release",
    "release-group",
    "series",
    "tag",
    "work",
    "url",
]

# Runtime views of the Literal types above (single source of truth).
CORE_ENTITIES: tuple[CoreEntity, ...] = get_args(CoreEntity)
ENTITIES: tuple[Entity, ...] = get_args(Entity)
SEARCH_ENTITIES: tuple[SearchEntity, ...] = get_args(SearchEntity)

#: JSON list key for each entity type.
PLURALS: dict[str, str] = {
    "annotation": "annotations",
    "area": "areas",
    "artist": "artists",
    "cdstub": "cdstubs",
    "collection": "collections",
    "event": "events",
    "genre": "genres",
    "instrument": "instruments",
    "label": "labels",
    "place": "places",
    "recording": "recordings",
    "release": "releases",
    "release-group": "release-groups",
    "series": "series",
    "tag": "tags",
    "work": "works",
    "url": "urls",
}

#: For each browsable entity, the linked entities it can be browsed by.
BROWSE_LINKS: dict[Entity, frozenset[str]] = {
    "area": frozenset({"collection"}),
    "artist": frozenset(
        {"area", "collection", "recording", "release", "release-group", "work"}
    ),
    "collection": frozenset(
        {
            "area",
            "artist",
            "editor",
            "event",
            "label",
            "place",
            "recording",
            "release",
            "release-group",
            "work",
        }
    ),
    "event": frozenset({"area", "artist", "collection", "event", "place"}),
    "genre": frozenset({"collection"}),
    "instrument": frozenset({"collection"}),
    "label": frozenset({"area", "collection", "release"}),
    "place": frozenset({"area", "collection"}),
    "recording": frozenset({"artist", "collection", "release", "work"}),
    "release": frozenset(
        {
            "area",
            "artist",
            "collection",
            "label",
            "track",
            "track_artist",
            "recording",
            "release-group",
        }
    ),
    "release-group": frozenset({"artist", "collection", "release"}),
    "series": frozenset({"collection"}),
    "work": frozenset({"artist", "collection"}),
    "url": frozenset({"resource"}),
}

RELATION_INCLUDES: frozenset[str] = frozenset(
    {
        "area-rels",
        "artist-rels",
        "event-rels",
        "genre-rels",
        "instrument-rels",
        "label-rels",
        "place-rels",
        "recording-rels",
        "release-rels",
        "release-group-rels",
        "series-rels",
        "url-rels",
        "work-rels",
        # level switches (only meaningful together with a *-rels include)
        "recording-level-rels",
        "release-group-level-rels",
        "work-level-rels",
    }
)

_TAGS = frozenset({"tags", "user-tags", "genres", "user-genres"})
_RATINGS = frozenset({"ratings", "user-ratings"})
_COMMON = frozenset({"aliases", "annotation"}) | _TAGS | RELATION_INCLUDES

#: Includes accepted on lookups (and the equivalent non-MBID lookups).
LOOKUP_INCLUDES: dict[str, frozenset[str]] = {
    "area": _COMMON,
    "artist": _COMMON
    | _RATINGS
    | {
        "recordings",
        "releases",
        "release-groups",
        "works",
        "various-artists",
        "discids",
        "media",
        "isrcs",
        "artist-credits",
    },
    "event": _COMMON | _RATINGS,
    "genre": _COMMON,
    "instrument": _COMMON | _RATINGS,
    "label": _COMMON | _RATINGS | {"releases", "discids", "media", "artist-credits"},
    "place": _COMMON,
    "recording": _COMMON
    | _RATINGS
    | {
        "artists",
        "releases",
        "release-groups",
        "discids",
        "media",
        "artist-credits",
        "isrcs",
    },
    "release": _COMMON
    | {
        "artists",
        "collections",
        "user-collections",
        "labels",
        "recordings",
        "release-groups",
        "media",
        "artist-credits",
        "discids",
        "isrcs",
    },
    "release-group": _COMMON
    | _RATINGS
    | {"artists", "releases", "discids", "media", "artist-credits"},
    "series": _COMMON,
    "work": _COMMON | _RATINGS,
    "url": RELATION_INCLUDES,
    "collection": frozenset(
        {"user-collections", "tags", "user-tags", "genres", "user-genres"}
    ),
}
LOOKUP_INCLUDES["discid"] = LOOKUP_INCLUDES["release"]
LOOKUP_INCLUDES["isrc"] = LOOKUP_INCLUDES["recording"]
LOOKUP_INCLUDES["iswc"] = LOOKUP_INCLUDES["work"]

#: Includes accepted on browse requests.
BROWSE_INCLUDES: dict[str, frozenset[str]] = {
    "area": _COMMON,
    "artist": _COMMON | _RATINGS,
    "collection": frozenset(
        {"user-collections", "tags", "user-tags", "genres", "user-genres"}
    ),
    "event": _COMMON | _RATINGS,
    "genre": _COMMON,
    "instrument": _COMMON | _RATINGS,
    "label": _COMMON | _RATINGS,
    "place": _COMMON,
    "recording": _COMMON | _RATINGS | {"artist-credits", "isrcs"},
    "release": _COMMON
    | {
        "artist-credits",
        "labels",
        "recordings",
        "release-groups",
        "media",
        "discids",
        "isrcs",
    },
    "release-group": _COMMON | _RATINGS | {"artist-credits"},
    "series": _COMMON,
    "work": _COMMON | _RATINGS,
    "url": RELATION_INCLUDES,
}

#: Includes that only work with authentication.
AUTH_INCLUDES: frozenset[str] = frozenset(
    {"user-tags", "user-ratings", "user-genres", "user-collections"}
)

RELEASE_GROUP_PRIMARY_TYPES: frozenset[str] = frozenset(
    {"album", "single", "ep", "broadcast", "other"}
)
RELEASE_GROUP_SECONDARY_TYPES: frozenset[str] = frozenset(
    {
        "audio drama",
        "audiobook",
        "compilation",
        "demo",
        "dj-mix",
        "field recording",
        "interview",
        "live",
        "mixtape/street",
        "remix",
        "soundtrack",
        "spokenword",
    }
)
RELEASE_TYPES: frozenset[str] = (
    RELEASE_GROUP_PRIMARY_TYPES | RELEASE_GROUP_SECONDARY_TYPES
)
RELEASE_STATUSES: frozenset[str] = frozenset(
    {
        "official",
        "promotion",
        "bootleg",
        "pseudo-release",
        "withdrawn",
        "expunged",
        "cancelled",
    }
)
RELEASE_GROUP_STATUSES: frozenset[str] = frozenset({"website-default", "all"})

#: Entity types that can be stored in collections (as used in collection URLs).
COLLECTION_ENTITY_TYPES: dict[str, str] = {
    "area": "areas",
    "artist": "artists",
    "event": "events",
    "genre": "genres",
    "instrument": "instruments",
    "label": "labels",
    "place": "places",
    "recording": "recordings",
    "release": "releases",
    "release-group": "release-groups",
    "series": "series",
    "work": "works",
}

#: Entities that accept tag submissions.
TAGGABLE_ENTITIES: frozenset[str] = frozenset(CORE_ENTITIES) - {"url", "genre"}
#: Entities that accept rating submissions.
RATABLE_ENTITIES: frozenset[str] = frozenset(
    {"artist", "event", "label", "recording", "release-group", "work"}
)

#: Max entities per collection add/remove request (per the API docs).
COLLECTION_BATCH_SIZE = 400
#: Max `resource=` parameters per URL lookup.
URL_BATCH_SIZE = 100
MAX_LIMIT = 100
