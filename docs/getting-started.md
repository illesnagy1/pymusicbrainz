# Getting started

## Create a client

```python
from musicbrainz import MusicBrainz

mb = MusicBrainz("MyApp", "1.0", "me@example.com")
```

The three arguments build the `User-Agent` header, which
[MusicBrainz requires](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting#Provide_meaningful_User-Agent_strings):

| Argument | Purpose |
|---|---|
| `app` | Your application's name. |
| `version` | Your application's version. Avoid hyphens if you plan to submit data, because the API rejects client IDs whose version contains one. |
| `contact` | Optional, but recommended: a URL or e-mail address where MusicBrainz admins can reach you. |

The example above sends `MyApp/1.0 ( me@example.com ) python-musicbrainz/0.1.0`.

Use the client as a context manager, or call `close()` when you're done, so
the underlying HTTP connections are released:

```python
with MusicBrainz("MyApp", "1.0", "me@example.com") as mb:
    ...
```

## Entity namespaces

Each entity type has a namespace on the client:

| Namespace | Entity | Lookup | Browse | Search |
|---|---|:-:|:-:|:-:|
| `mb.areas` | area | ✓ | ✓ | ✓ |
| `mb.artists` | artist | ✓ | ✓ | ✓ |
| `mb.events` | event | ✓ | ✓ | ✓ |
| `mb.genres` | genre | ✓ | ✓ | |
| `mb.instruments` | instrument | ✓ | ✓ | ✓ |
| `mb.labels` | label | ✓ | ✓ | ✓ |
| `mb.places` | place | ✓ | ✓ | ✓ |
| `mb.recordings` | recording | ✓ | ✓ | ✓ |
| `mb.releases` | release | ✓ | ✓ | ✓ |
| `mb.release_groups` | release-group | ✓ | ✓ | ✓ |
| `mb.series` | series | ✓ | ✓ | ✓ |
| `mb.works` | work | ✓ | ✓ | ✓ |
| `mb.urls` | url | ✓ | ✓ | ✓ |
| `mb.collections` | collection | ✓ | ✓ | |
| `mb.annotations`, `mb.cdstubs`, `mb.tags` | | | | ✓ |

Every namespace with lookup and browse has the same methods:

```python
mb.artists.get(mbid, inc=...)  # one entity
mb.artists.browse(area=mbid)  # one page of linked entities
mb.artists.iter_browse(area=mbid)  # all of them, page by page
mb.artists.search("query", country="US")  # one page of search results
mb.artists.iter_search("query")  # all search results
```

If the entity type is only known at runtime, `mb.resource("release-group")`
returns the matching namespace. Plural names such as `"releases"` work too.

## Results are typed

Lookups return [pydantic](https://docs.pydantic.dev/) models with snake_case
attributes. The API's kebab-case keys (`sort-name`, `life-span`) map onto
them automatically:

```python
artist = mb.artists.get("5b11f4ce-a62d-471e-81fc-a69a8278c7da")
artist.name  # 'Nirvana'
artist.sort_name  # 'Nirvana'
artist.life_span.end  # '1994-04-05'
```

Fields the models don't define are kept rather than dropped. They are
available in `model.model_extra`.

Browse and search return a [`Page`][musicbrainz.models.Page]: a list of
models plus the total `count`, the `offset` and a `has_more` flag.

## What next

- [Lookups & includes](guide/lookups.md): fetch one entity with related data.
- [Browse & pagination](guide/browse.md): list everything linked to an entity.
- [Search](guide/search.md): full-text search with Lucene queries.
