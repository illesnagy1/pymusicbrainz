# Search

Search uses the MusicBrainz search indexes, which accept
[Lucene query syntax](https://musicbrainz.org/doc/MusicBrainz_API/Search):

```python
page = mb.artists.search("nirvana")
for artist in page:
    print(artist.score, artist.name, artist.disambiguation)
```

Each result has a `score` from 0 to 100. `page.count` is the total number of
matches.

## Field searches

Keyword arguments become `field:"value"` terms, joined with `AND`:

```python
mb.release_groups.search(artist="Nirvana", primarytype="album")
# query: artist:"Nirvana" AND primarytype:"album"

mb.releases.search("nevermind", country="US", format="CD")
# query: (nevermind) AND country:"US" AND format:"CD"
```

| Value | Becomes |
|---|---|
| a string | an exact phrase: `field:"value"` |
| a list, tuple or set | an OR group: `(field:"a" OR field:"b")` |
| a number or bool | a bare term: `dur:1000`, `video:false` |
| `None` | nothing; the field is skipped |

The available fields differ per entity. Field names are checked before the
request is sent, so a typo fails loudly instead of silently matching nothing:

```python
mb.artists.search(artsit="Nirvana")
# ValidationError: invalid search field(s) for artist: artsit. Allowed: alias, area, ...
```

The full lists are in
[`SEARCH_FIELDS`][pymusicbrainz._constants.SEARCH_FIELDS], taken from the
[MusicBrainz search documentation](https://musicbrainz.org/doc/MusicBrainz_API/Search).
If MusicBrainz adds a field before this library knows about it, create the
client with `validate=False`, or put the term in the `query` string, which is
never checked. A few common fields:

| Entity | Fields |
|---|---|
| artist | `artist`, `alias`, `country`, `type`, `gender`, `begin`, `end`, `tag` |
| release | `release`, `artist`, `label`, `barcode`, `catno`, `country`, `date`, `format`, `status` |
| release-group | `releasegroup`, `artist`, `primarytype`, `secondarytype`, `firstreleasedate` |
| recording | `recording`, `artist`, `release`, `isrc`, `dur`, `video` |
| work | `work`, `artist`, `iswc`, `lang` |

## Escaping

Free text passed as `query` is used as-is, so Lucene operators work. To search
for text that contains special characters, escape it, or use the dismax
parser, which treats the query as plain text:

```python
from pymusicbrainz import escape

mb.artists.search(escape("AC/DC"))
mb.artists.search("AC/DC", dismax=True)
```

`pymusicbrainz.query.build_query()` and `any_of()` build queries without
sending them; see the [query reference](../reference/query.md).

## All results

`iter_search` pages through the results; `max_items=` caps how many you get:

```python
for recording in mb.recordings.iter_search(artist="Nirvana", max_items=200):
    ...
```

## Search-only indexes

`mb.annotations`, `mb.cdstubs` and `mb.tags` support `search` and
`iter_search` only:

```python
mb.tags.search("shoegaze", limit=5)
```
