# pymusicbrainz

A typed Python client for the
[MusicBrainz WS/2 API](https://musicbrainz.org/doc/MusicBrainz_API) and the
[Cover Art Archive](https://coverartarchive.org/), with sync and async clients,
rate limiting and retries built in, and a JSON command-line tool.

```python
from pymusicbrainz import MusicBrainz

with MusicBrainz("MyApp", "1.0", "me@example.com") as mb:
    nirvana = mb.artists.search(artist="Nirvana", country="US", limit=1)[0]

    for rg in mb.release_groups.iter_browse(artist=nirvana.id, release_type="album"):
        print(rg.first_release_date, rg.title)
```

```sh
pymusicbrainz search artist "Daft Punk" --limit 3
```

## Installation

Requires Python 3.12 or later.

```sh
uv add pymusicbrainz            # the library
uv add 'pymusicbrainz[cli]'     # plus the pymusicbrainz command
```

Runnable scripts are in [`examples/`](https://github.com/illesnagy1/pymusicbrainz/tree/main/examples): an artist's discography,
cover art download, identifying a CD, concurrent ISRC lookups, and collection
export.

## Documentation

The full guide and API reference are at
<https://illesnagy1.github.io/pymusicbrainz/>. To browse them locally:

```sh
uv run zensical serve
```

## Development

```sh
uv sync
uv run pytest        # offline tests
uv run ruff check .  # lint
uv run ty check      # type check
```

See the [development guide](https://illesnagy1.github.io/pymusicbrainz/development/) for the architecture and
conventions.
