# Other lookups

Some identifiers aren't MBIDs. These lookups live on the client itself.

## Disc IDs

A [disc ID](https://musicbrainz.org/doc/Disc_ID) identifies a CD by its table
of contents:

```python
from pymusicbrainz import CDStub, Disc

result = mb.lookup_discid("2y0jFMwYQP7gb9iSUKagmoT6DIw-", inc="artist-credits")
if isinstance(result, Disc):
    for release in result.releases:
        print(release.title)
elif isinstance(result, CDStub):
    print("only a CD stub:", result.title, "by", result.artist)
```

The result is a [`Disc`][pymusicbrainz.models.Disc] with the matching releases,
or a [`CDStub`][pymusicbrainz.models.CDStub] if only an unreviewed stub
matches. `inc` accepts the same values as a release lookup.

| Argument | Effect |
|---|---|
| `toc=` | Fuzzy-match by table of contents when the disc ID is unknown. Pass a string or a list of integers; use `"-"` as the disc ID to search by TOC only. |
| `cdstubs=False` | Don't return CD stubs. |
| `all_media_formats=True` | Fuzzy-match all media formats, not just CDs. |
| `release_type=`, `release_status=` | Filter the releases. |

## ISRC and ISWC

```python
result = mb.lookup_isrc("USGF19942501")  # IsrcResult
for recording in result.recordings:
    print(recording.title)

works = mb.lookup_iswc("T-070.243.691-6")  # list[Work]
```

Codes are normalised first: `"us-gf1-99-42501"` and `"T0702436916"` are
accepted. Malformed codes raise `ValidationError`.

## URLs

URL entities are looked up by the URL text itself:

```python
url = mb.urls.by_resource("https://www.discogs.com/artist/125246", inc="artist-rels")
for rel in url.relations:
    print(rel.artist.name)
```

`by_resource` raises `NotFoundError` for an unknown URL. `by_resources`
takes many URLs, sends them in batches of 100, and skips the unknown ones:

```python
known = mb.urls.by_resources(list_of_urls)
```

## Genres

```python
names = mb.genres.names()  # every genre name, one request
page = mb.genres.all(limit=100)  # Genre models, alphabetical
for genre in mb.genres.iter_all():
    ...
```
