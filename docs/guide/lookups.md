# Lookups & includes

A lookup fetches one entity by its MBID:

```python
artist = mb.artists.get("5b11f4ce-a62d-471e-81fc-a69a8278c7da")
release = mb.releases.get(release_mbid)
```

An invalid MBID raises [`ValidationError`][musicbrainz.errors.ValidationError]
before any request is sent. An unknown one raises
[`NotFoundError`][musicbrainz.errors.NotFoundError].

## Includes

By default a lookup returns only the entity's own fields. Use `inc` to pull in
related data in the same request:

```python
artist = mb.artists.get(mbid, inc=["aliases", "url-rels", "genres"])
```

`inc` takes a list, or a string separated by `+`, spaces, commas or `|`. These
are all equivalent:

```python
mb.artists.get(mbid, inc=["aliases", "url-rels"])
mb.artists.get(mbid, inc="aliases+url-rels")
mb.artists.get(mbid, inc="aliases url-rels")
mb.artists.get(mbid, inc=["aliases", "url-rels,tags"])  # list items are split too
```

The includes fall into four groups.

**Linked entities.** These are capped at 25 items; use
[browse](browse.md) to get them all.

| Entity | Includes |
|---|---|
| artist | `recordings`, `releases`, `release-groups`, `works`, `various-artists` |
| label | `releases` |
| recording | `artists`, `releases`, `release-groups` |
| release | `artists`, `labels`, `recordings`, `release-groups`, `collections` |
| release-group | `artists`, `releases` |

**Detail for linked entities:** `artist-credits`, `media`, `discids` and
`isrcs`.

**Miscellaneous:** `aliases`, `annotation`, `tags`, `genres` and `ratings`
(ratings are not available for area, place, release or series). The `user-*`
variants (`user-tags`, `user-genres`, `user-ratings`, `user-collections`)
need [authentication](collections-submissions.md#authentication).

**Relationships:** `artist-rels`, `release-rels`, `url-rels`, `work-rels` and
so on, one per entity type. Add `recording-level-rels`,
`release-group-level-rels` or `work-level-rels` to get relationships of the
nested entities as well.

The complete per-entity list is in
[`LOOKUP_INCLUDES`][musicbrainz._constants.LOOKUP_INCLUDES]. An include that
doesn't apply to the entity raises `ValidationError` and lists the allowed
values. If MusicBrainz adds an include this library doesn't know about yet,
create the client with `validate=False`.

Some includes only work together with another one. The client checks these
combinations before sending:

| Entity | Include | Also needs one of |
|---|---|---|
| artist | `isrcs` | `recordings` |
| artist, label, recording, release-group | `media`, `discids` | `releases` |
| artist | `various-artists` | `releases` |
| artist | `artist-credits` | `releases`, `recordings`, `release-groups`, `works`, or a `release-rels`/`recording-rels`/`release-group-rels` include |
| label | `artist-credits` | `releases`, or a `release-rels`/`recording-rels`/`release-group-rels` include |
| release | `isrcs` | `recordings` |
| release | `recording-level-rels` | `recordings` |
| any | `*-level-rels` | a relationship include such as `artist-rels` |

```python
mb.artists.get(mbid, inc="isrcs")
# ValidationError: include 'isrcs' for artist also needs 'recordings'
mb.artists.get(mbid, inc=["recordings", "isrcs"])  # fine
```

Most of these the server would reject with a 400 anyway; the check fails
before a rate-limited request is spent. The `*-level-rels` cases matter more:
the server accepts them but silently returns nothing extra.

## Filtering included releases

When `releases` or `release-groups` are included, `release_type` and
`release_status` narrow them down:

```python
artist = mb.artists.get(
    mbid,
    inc=["release-groups"],
    release_type=["album", "ep"],
)
official = mb.artists.get(mbid, inc="releases", release_status="official")
```

Types are the release group's primary and secondary types (`album`, `single`,
`ep`, `live`, `compilation`, …); statuses are `official`, `promotion`,
`bootleg`, `pseudo-release`, `withdrawn`, `expunged` and `cancelled`. Both
accept a single value or a list.

## Relationships

Relationship includes fill the `relations` list. Each
[`Relation`][musicbrainz.models.Relation] has a `type`, a `target_type` and
the related entity in the attribute of that name:

```python
artist = mb.artists.get(mbid, inc="url-rels")

for rel in artist.relations_of("url"):
    print(rel.type, rel.url.resource)

homepage = artist.relations_of("url", "official homepage")
```

`rel.target` returns the related entity whatever its type.

## Artist credits

Recordings, releases and release groups carry an `artist_credit` list (with
`inc="artist-credits"` where needed). `artist_credit_phrase` renders it the
way the website does:

```python
release = mb.releases.get(mbid, inc="artist-credits")
release.artist_credit_phrase  # 'Simon & Garfunkel'
```
