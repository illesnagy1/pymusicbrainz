# Browse & pagination

Browsing lists the entities directly linked to another entity, for example all
releases by an artist or all recordings on a release. Pass the linked entity
as a keyword argument:

```python
page = mb.releases.browse(artist="5b11f4ce-a62d-471e-81fc-a69a8278c7da")
page.count  # total number of releases by the artist
len(page)  # number on this page (25 by default, at most 100)
```

Exactly one linked entity is allowed per request. Use `release_group=` for
release groups; `track_artist=` keeps its underscore.

| Browse | Linked by |
|---|---|
| `areas` | `collection` |
| `artists` | `area`, `collection`, `recording`, `release`, `release_group`, `work` |
| `collections` | `area`, `artist`, `editor`, `event`, `label`, `place`, `recording`, `release`, `release_group`, `work` |
| `events` | `area`, `artist`, `collection`, `event`, `place` |
| `genres`, `instruments`, `series` | `collection` |
| `labels` | `area`, `collection`, `release` |
| `places` | `area`, `collection` |
| `recordings` | `artist`, `collection`, `release`, `work` |
| `releases` | `area`, `artist`, `collection`, `label`, `track`, `track_artist`, `recording`, `release_group` |
| `release_groups` | `artist`, `collection`, `release` |
| `works` | `artist`, `collection` |

`track_artist` finds releases where the artist appears on a track but not in
the release's own artist credit.

## Filters and includes

Release and release group browses accept `release_type` and `release_status`.
Browsing release groups by artist also accepts `release_group_status`
(`"website-default"` or `"all"`).

```python
albums = mb.release_groups.browse(artist=mbid, release_type="album")
bootlegs = mb.releases.browse(
    artist=mbid, release_type="live", release_status="bootleg"
)
```

Includes work as in [lookups](lookups.md#includes), but the allowed set is
different: see
[`BROWSE_INCLUDES`][musicbrainz._constants.BROWSE_INCLUDES].

## Paging

`limit` (1–100) and `offset` select a page:

```python
page = mb.releases.browse(artist=mbid, limit=100, offset=100)
if page.has_more:
    next_page = mb.releases.browse(artist=mbid, limit=100, offset=page.next_offset)
```

Usually it's simpler to let `iter_browse` fetch every page:

```python
for release in mb.releases.iter_browse(artist=mbid, release_status="official"):
    print(release.date, release.title)
```

It requests 100 items at a time (`page_size=`), and `max_items=` stops early.
Invalid arguments raise immediately, not on the first iteration.

!!! note "Release pages can be short"

    MusicBrainz caps a release page at 500 tracks in total, so a page can hold
    fewer releases than `limit`. `next_offset` and `iter_browse` advance by the
    number of items actually returned, so nothing is skipped.

## Collection contents

Browsing by `collection=` lists what's in a collection. The
[collections namespace](collections-submissions.md#collections) has
shortcuts for this.
