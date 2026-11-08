"""Typed pydantic models for MusicBrainz JSON responses.

Every model keeps unknown fields (``extra="allow"``), so nothing the server sends
is lost, even if a field is not modelled here. Access them with
``model.model_extra`` or ``getattr``. JSON keys are kebab-case (``sort-name``);
Python attributes are snake_case (``sort_name``).
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict


def _kebab(name: str) -> str:
    return name.replace("_", "-")


class MBModel(BaseModel):
    """Base for all models: kebab-case JSON keys, unknown fields kept."""

    model_config = ConfigDict(
        alias_generator=_kebab,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        extra="allow",
    )


# ------------------------------------------------------------------ value objects


class LifeSpan(MBModel):
    """Begin/end dates (partial dates such as ``"1991"`` or ``"1991-09"``)."""

    begin: str | None = None
    end: str | None = None
    ended: bool | None = None


class Alias(MBModel):
    """An alternative name, e.g. a translation or a legal name."""

    name: str
    sort_name: str | None = None
    locale: str | None = None
    type: str | None = None
    type_id: str | None = None
    primary: bool | None = None
    begin: str | None = None
    end: str | None = None
    ended: bool | None = None


class Tag(MBModel):
    """A folksonomy tag with its vote count (or ``score`` in tag search results)."""

    name: str
    count: int | None = None
    score: int | None = None  # tag search results


class Genre(MBModel):
    """A genre: a tag from MusicBrainz's curated genre list."""

    id: str | None = None
    name: str
    count: int | None = None
    disambiguation: str | None = None


class Rating(MBModel):
    """Average rating (0-5) and the number of votes."""

    value: float | None = None
    votes_count: int | None = None


class Coordinates(MBModel):
    """Latitude and longitude of a place."""

    latitude: float | None = None
    longitude: float | None = None


class TextRepresentation(MBModel):
    """Language and script of a release's titles."""

    language: str | None = None
    script: str | None = None


class CoverArtArchive(MBModel):
    """What the Cover Art Archive holds for a release."""

    artwork: bool | None = None
    count: int | None = None
    front: bool | None = None
    back: bool | None = None
    darkened: bool | None = None


class WorkAttribute(MBModel):
    """A typed attribute of a work, e.g. a key or a catalogue number."""

    type: str | None = None
    type_id: str | None = None
    value: str | None = None
    value_id: str | None = None


# ------------------------------------------------------------------ relationships


class Relation(MBModel):
    """A relationship to another entity (from ``*-rels`` includes)."""

    type: str | None = None
    type_id: str | None = None
    direction: str | None = None
    target_type: str | None = None
    target_credit: str | None = None
    source_credit: str | None = None
    attributes: list[str] = []
    attribute_ids: dict[str, str] = {}
    attribute_values: dict[str, Any] = {}
    attribute_credits: dict[str, Any] = {}
    begin: str | None = None
    end: str | None = None
    ended: bool | None = None
    ordering_key: int | None = None

    area: Area | None = None
    artist: Artist | None = None
    event: Event | None = None
    genre: Genre | None = None
    instrument: Instrument | None = None
    label: Label | None = None
    place: Place | None = None
    recording: Recording | None = None
    release: Release | None = None
    release_group: ReleaseGroup | None = None
    series: Series | None = None
    url: Url | None = None
    work: Work | None = None

    @property
    def target(self) -> BaseModel | None:
        """The related entity, whatever its type."""
        if self.target_type:
            return getattr(self, self.target_type.replace("-", "_"), None)
        return None


# --------------------------------------------------------------------------- entities


class Entity(MBModel):
    """Fields common to all core entities."""

    id: str
    score: int | None = None  # search results only
    disambiguation: str | None = None
    annotation: str | None = None
    aliases: list[Alias] = []
    tags: list[Tag] = []
    user_tags: list[Tag] = []
    genres: list[Genre] = []
    user_genres: list[Genre] = []
    rating: Rating | None = None
    user_rating: Rating | None = None
    relations: list[Relation] = []

    def relations_of(
        self, target_type: str, relation_type: str | None = None
    ) -> list[Relation]:
        """Relationships to ``target_type`` entities, optionally of one type."""
        return [
            r
            for r in self.relations
            if r.target_type == target_type
            and (relation_type is None or r.type == relation_type)
        ]


class Area(Entity):
    """A geographic area: country, city, district, ..."""

    name: str | None = None
    sort_name: str | None = None
    type: str | None = None
    type_id: str | None = None
    iso_3166_1_codes: list[str] = []
    iso_3166_2_codes: list[str] = []
    iso_3166_3_codes: list[str] = []
    life_span: LifeSpan | None = None


class Artist(Entity):
    """A person, group, orchestra, character, ..."""

    name: str | None = None
    sort_name: str | None = None
    type: str | None = None
    type_id: str | None = None
    gender: str | None = None
    gender_id: str | None = None
    country: str | None = None
    area: Area | None = None
    begin_area: Area | None = None
    end_area: Area | None = None
    life_span: LifeSpan | None = None
    ipis: list[str] = []
    isnis: list[str] = []
    recordings: list[Recording] = []
    releases: list[Release] = []
    release_groups: list[ReleaseGroup] = []
    works: list[Work] = []


class ArtistCredit(MBModel):
    """One name in an artist credit, with its join phrase."""

    name: str | None = None
    joinphrase: str = ""
    artist: Artist | None = None


def credit_string(credit: list[ArtistCredit]) -> str:
    """Render an artist credit as shown on the site, e.g. ``"Simon & Garfunkel"``."""
    return "".join(
        f"{c.name or (c.artist.name if c.artist else '')}{c.joinphrase}" for c in credit
    )


class Event(Entity):
    """A concert, festival or other organised event."""

    name: str | None = None
    type: str | None = None
    type_id: str | None = None
    time: str | None = None
    cancelled: bool | None = None
    setlist: str | None = None
    life_span: LifeSpan | None = None


class Instrument(Entity):
    """A musical instrument."""

    name: str | None = None
    type: str | None = None
    type_id: str | None = None
    description: str | None = None


class Label(Entity):
    """A record label or imprint."""

    name: str | None = None
    sort_name: str | None = None
    type: str | None = None
    type_id: str | None = None
    label_code: int | None = None
    country: str | None = None
    area: Area | None = None
    life_span: LifeSpan | None = None
    ipis: list[str] = []
    isnis: list[str] = []
    releases: list[Release] = []


class Place(Entity):
    """A venue, studio or other place."""

    name: str | None = None
    type: str | None = None
    type_id: str | None = None
    address: str | None = None
    coordinates: Coordinates | None = None
    area: Area | None = None
    life_span: LifeSpan | None = None


class Recording(Entity):
    """A distinct audio recording (one track's audio)."""

    title: str | None = None
    length: int | None = None  # milliseconds
    video: bool | None = None
    first_release_date: str | None = None
    artist_credit: list[ArtistCredit] = []
    isrcs: list[str] = []
    releases: list[Release] = []
    release_groups: list[ReleaseGroup] = []

    @property
    def artist_credit_phrase(self) -> str:
        return credit_string(self.artist_credit)


class Track(MBModel):
    """A track on a medium, linking to its recording."""

    id: str | None = None
    number: str | None = None
    title: str | None = None
    position: int | None = None
    length: int | None = None
    recording: Recording | None = None
    artist_credit: list[ArtistCredit] = []


class Disc(MBModel):
    """A disc ID and the releases it belongs to.

    Returned by
    [`MusicBrainz.lookup_discid`][musicbrainz.client.MusicBrainz.lookup_discid].
    """

    id: str | None = None
    sectors: int | None = None
    offset_count: int | None = None
    offsets: list[int] = []
    releases: list[Release] = []


class Medium(MBModel):
    """One disc, side or other medium of a release."""

    id: str | None = None
    title: str | None = None
    position: int | None = None
    format: str | None = None
    format_id: str | None = None
    track_count: int | None = None
    track_offset: int | None = None
    tracks: list[Track] = []
    data_tracks: list[Track] = []
    pregap: Track | None = None
    discs: list[Disc] = []


class LabelInfo(MBModel):
    """A label and catalogue number of a release."""

    catalog_number: str | None = None
    label: Label | None = None


class ReleaseEvent(MBModel):
    """A release date in an area."""

    date: str | None = None
    area: Area | None = None


class Release(Entity):
    """A concrete product: an album edition, single, ..."""

    title: str | None = None
    status: str | None = None
    status_id: str | None = None
    quality: str | None = None
    packaging: str | None = None
    packaging_id: str | None = None
    date: str | None = None
    country: str | None = None
    barcode: str | None = None
    asin: str | None = None
    text_representation: TextRepresentation | None = None
    artist_credit: list[ArtistCredit] = []
    release_group: ReleaseGroup | None = None
    label_info: list[LabelInfo] = []
    media: list[Medium] = []
    release_events: list[ReleaseEvent] = []
    cover_art_archive: CoverArtArchive | None = None
    track_count: int | None = None
    collections: list[Collection] = []

    @property
    def artist_credit_phrase(self) -> str:
        return credit_string(self.artist_credit)


class ReleaseGroup(Entity):
    """Groups releases of the same album, single, ..."""

    title: str | None = None
    primary_type: str | None = None
    primary_type_id: str | None = None
    secondary_types: list[str] = []
    secondary_type_ids: list[str] = []
    first_release_date: str | None = None
    artist_credit: list[ArtistCredit] = []
    releases: list[Release] = []

    @property
    def artist_credit_phrase(self) -> str:
        return credit_string(self.artist_credit)


class Series(Entity):
    """A sequence of works, releases, events, ..."""

    name: str | None = None
    type: str | None = None
    type_id: str | None = None


class Work(Entity):
    """A musical composition."""

    title: str | None = None
    type: str | None = None
    type_id: str | None = None
    iswcs: list[str] = []
    language: str | None = None
    languages: list[str] = []
    attributes: list[WorkAttribute] = []


class Url(Entity):
    """A URL entity."""

    resource: str | None = None


class Collection(MBModel):
    """A user's collection of entities."""

    id: str
    name: str | None = None
    editor: str | None = None
    type: str | None = None
    type_id: str | None = None
    entity_type: str | None = None

    @property
    def item_count(self) -> int | None:
        """Number of items in the collection (``<entity>-count`` in the JSON)."""
        for key, value in (self.model_extra or {}).items():
            if key.endswith("-count") and isinstance(value, int):
                return value
        return None


class Annotation(MBModel):
    """A search result from the annotation index."""

    entity: str | None = None
    name: str | None = None
    text: str | None = None
    type: str | None = None
    score: int | None = None


class CDStub(MBModel):
    """An unreviewed CD stub (returned when no release matches a disc ID)."""

    id: str | None = None
    title: str | None = None
    artist: str | None = None
    barcode: str | None = None
    comment: str | None = None
    count: int | None = None
    score: int | None = None
    tracks: list[dict[str, Any]] = []


class IsrcResult(MBModel):
    """Result of an ISRC lookup: the code and its recordings."""

    isrc: str
    recordings: list[Recording] = []


for _m in list(globals().values()):
    if isinstance(_m, type) and issubclass(_m, MBModel):
        _m.model_rebuild()
del _m


# ------------------------------------------------------------------ pagination


@dataclass(slots=True)
class Page[T]:
    """One page of a browse or search response."""

    items: list[T]
    count: int
    offset: int
    created: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> T:
        return self.items[index]

    @property
    def next_offset(self) -> int:
        return self.offset + len(self.items)

    @property
    def has_more(self) -> bool:
        return bool(self.items) and self.next_offset < self.count


ENTITY_MODELS: dict[str, type[MBModel]] = {
    "annotation": Annotation,
    "area": Area,
    "artist": Artist,
    "cdstub": CDStub,
    "collection": Collection,
    "event": Event,
    "genre": Genre,
    "instrument": Instrument,
    "label": Label,
    "place": Place,
    "recording": Recording,
    "release": Release,
    "release-group": ReleaseGroup,
    "series": Series,
    "tag": Tag,
    "url": Url,
    "work": Work,
}
