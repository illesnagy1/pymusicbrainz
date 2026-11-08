# musicbrainz

A typed Python client for the
[MusicBrainz WS/2 API](https://musicbrainz.org/doc/MusicBrainz_API), with
sync and async clients, rate limiting and retries built in, and a JSON
command-line tool.

```python
from musicbrainz import MusicBrainz

with MusicBrainz("MyApp", "1.0", "me@example.com") as mb:
    nirvana = mb.artists.search(artist="Nirvana", country="US", limit=1)[0]

    for rg in mb.release_groups.iter_browse(artist=nirvana.id, release_type="album"):
        print(rg.first_release_date, rg.title)
```

```sh
musicbrainz search artist "Daft Punk" --limit 3
```

## Installation

Requires Python 3.15 or later.

```sh
uv add musicbrainz
```

## Documentation

The full guide and API reference are in [`docs/`](docs/index.md). To browse
them locally:

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

See [docs/development.md](docs/development.md) for the architecture and
conventions.
