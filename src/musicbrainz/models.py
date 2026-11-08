"""Typed pydantic models for MusicBrainz JSON responses.

Every model keeps unknown fields (``extra="allow"``), so nothing the server sends is lost,
even if a field is not modelled here. Access them with ``model.model_extra`` or ``getattr``.
JSON keys are kebab-case (``sort-name``); Python attributes are snake_case (``sort_name``).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict


def _kebab(name: str) -> str:
    return name.replace("_", "-")


class MBModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_kebab,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        extra="allow",
    )


# --------------------------------------------------------------------------- small value objects


class LifeSpan(MBModel):
    begin: str | None = None
    end: str | None = None
    ended: bool | None = None


class Alias(MBModel):
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
    name: str
    count: int | None = None
    score: int | None = None  # tag search results


class Genre(MBModel):
    id: str | None = None
    name: str
    count: int | None = None
    disambiguation: str | None = None


class Rating(MBModel):
    value: float | None = None
    votes_count: int | None = None


class Coordinates(MBModel):
    latitude: float | None = None
    longitude: float | None = None


class TextRepresentation(MBModel):
    language: str | None = None
    script: str | None = None


class CoverArtArchive(MBModel):
    artwork: bool | None = None
    count: int | None = None
    front: bool | None = None
    back: bool | None = None
    darkened: bool | None = None


class WorkAttribute(MBModel):
    type: str | None = None
    type_id: str | None = None
    value: str | None = None
    value_id: str | None = None


# --------------------------------------------------------------------------- relationships


class Relation(MBModel):
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

    def relations_of(self, target_type: str, type: str | None = None) -> list[Relation]:
        """Relationships filtered by target entity type and optionally by relationship type."""
        return [
            r for r in self.relations
            if r.target_type == target_type and (type is None or r.type == type)
        ]


class Area(Entity):
    name: str | None = None
    sort_name: str | None = None
    type: str | None = None
    type_id: str | None = None
    iso_3166_1_codes: list[str] = []
    iso_3166_2_codes: list[str] = []
    iso_3166_3_codes: list[str] = []
    life_span: LifeSpan | None = None


class Artist(Entity):
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
    name: str | None = None
    joinphrase: str = ""
    artist: Artist | None = None


def credit_string(credits: list[ArtistCredit]) -> str:
    """Render an artist credit as displayed on the site, e.g. ``"Simon & Garfunkel"``."""
    return "".join(f"{c.name or (c.artist.name if c.artist else '')}{c.joinphrase}" for c in credits)


class Event(Entity):
    name: str | None = None
    type: str | None = None
    type_id: str | None = None
    time: str | None = None
    cancelled: bool | None = None
    setlist: str | None = None
    life_span: LifeSpan | None = None


class Instrument(Entity):
    name: str | None = None
    type: str | None = None
    type_id: str | None = None
    description: str | None = None


class Label(Entity):
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
    name: str | None = None
    type: str | None = None
    type_id: str | None = None
    address: str | None = None
    coordinates: Coordinates | None = None
    area: Area | None = None
    life_span: LifeSpan | None = None


class Recording(Entity):
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
    id: str | None = None
    number: str | None = None
    title: str | None = None
    position: int | None = None
    length: int | None = None
    recording: Recording | None = None
    artist_credit: list[ArtistCredit] = []


class Disc(MBModel):
    """A disc ID (also the result of :meth:`MusicBrainz.lookup_discid`)."""

    id: str | None = None
    sectors: int | None = None
    offset_count: int | None = None
    offsets: list[int] = []
    releases: list[Release] = []


class Medium(MBModel):
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
    catalog_number: str | None = None
    label: Label | None = None


class ReleaseEvent(MBModel):
    date: str | None = None
    area: Area | None = None


class Release(Entity):
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
    name: str | None = None
    type: str | None = None
    type_id: str | None = None


class Work(Entity):
    title: str | None = None
    type: str | None = None
    type_id: str | None = None
    iswcs: list[str] = []
    language: str | None = None
    languages: list[str] = []
    attributes: list[WorkAttribute] = []


class Url(Entity):
    resource: str | None = None


class Collection(MBModel):
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
    entity: str | None = None
    name: str | None = None
    text: str | None = None
    type: str | None = None
    score: int | None = None


class CDStub(MBModel):
    id: str | None = None
    title: str | None = None
    artist: str | None = None
    barcode: str | None = None
    comment: str | None = None
    count: int | None = None
    score: int | None = None
    tracks: list[dict[str, Any]] = []


class IsrcResult(MBModel):
    isrc: str
    recordings: list[Recording] = []


for _m in list(globals().values()):
    if isinstance(_m, type) and issubclass(_m, MBModel):
        _m.model_rebuild()
del _m


# --------------------------------------------------------------------------- pagination

T = TypeVar("T")


@dataclass(slots=True)
class Page(Generic[T]):
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
