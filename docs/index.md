# pymusicbrainz

A typed Python client for the
[MusicBrainz WS/2 API](https://musicbrainz.org/doc/MusicBrainz_API), with a
synchronous and an asynchronous client that share one implementation.

```python
from pymusicbrainz import MusicBrainz

with MusicBrainz("MyApp", "1.0", "me@example.com") as mb:
    nirvana = mb.artists.search(artist="Nirvana", country="US", limit=1)[0]

    for rg in mb.release_groups.iter_browse(artist=nirvana.id, release_type="album"):
        print(rg.first_release_date, rg.title)
```

## Features

<div class="grid cards" markdown>

-   :material-database-search: **The whole read API**

    ---

    Lookup, browse and search for every entity, plus disc ID, ISRC, ISWC and
    URL lookups and the genre list.

    [:octicons-arrow-right-24: Lookups](guide/lookups.md)

-   :material-shield-check: **Typed and validated**

    ---

    Pydantic models for every entity. Includes, filters, MBIDs, ISRCs, ISWCs
    and barcodes are checked before a request is sent.

    [:octicons-arrow-right-24: Models](reference/models.md)

-   :material-timer-sand: **A good API citizen**

    ---

    Rate limiting (1 request/second by default), retries with back-off, and
    the `User-Agent` MusicBrainz asks for.

    [:octicons-arrow-right-24: Reliability](guide/reliability.md)

-   :material-sync: **Sync and async**

    ---

    `MusicBrainz` and `AsyncMusicBrainz` have the same API and the same
    behaviour; the async client works with asyncio and trio.

    [:octicons-arrow-right-24: Async](guide/async.md)

-   :material-image: **Cover art**

    ---

    List and download release and release group images from the Cover Art
    Archive, including 250/500/1200 px thumbnails.

    [:octicons-arrow-right-24: Cover art](guide/cover-art.md)

-   :material-pencil: **Submissions**

    ---

    Tags, ratings, barcodes and ISRCs, plus collection management, with
    digest or OAuth2 authentication.

    [:octicons-arrow-right-24: Collections & submissions](guide/collections-submissions.md)

-   :material-console: **Command line**

    ---

    A `pymusicbrainz` command that prints JSON, for quick queries and shell
    scripts.

    [:octicons-arrow-right-24: CLI](cli.md)

</div>

## Installation

The package requires Python 3.12 or later.

=== "uv"

    ```sh
    uv add pymusicbrainz
    ```

=== "pip"

    ```sh
    pip install pymusicbrainz
    ```

Optional extras:

| Extra | Adds |
|---|---|
| `pymusicbrainz[cli]` | The [`pymusicbrainz` command](cli.md) (Typer and Rich). |
| `pymusicbrainz[socks]` | [SOCKS proxy](guide/reliability.md#proxies) support. |

Next: [Getting started](getting-started.md).
