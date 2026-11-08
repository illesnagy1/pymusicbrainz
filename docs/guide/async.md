# Async

[`AsyncMusicBrainz`][pymusicbrainz.client.AsyncMusicBrainz] has the same
constructor and methods as `MusicBrainz`. Request methods are awaited, and the
`iter_*` methods return async iterators:

```python
import anyio
from pymusicbrainz import AsyncMusicBrainz


async def main() -> None:
    async with AsyncMusicBrainz("MyApp", "1.0", "me@example.com") as mb:
        artist = (await mb.artists.search(artist="Radiohead", limit=1))[0]

        async for rg in mb.release_groups.iter_browse(
            artist=artist.id, release_type="album"
        ):
            print(rg.first_release_date, rg.title)


anyio.run(main)
```

It runs under asyncio and trio. Use `async with` or `await mb.aclose()` to
release connections.

## Concurrency and the rate limit

Starting many requests at once doesn't make them faster: the client's rate
limiter still spaces them about one second apart, in the order they acquire
the limiter. Concurrency helps when your own processing is slow, not with
throughput against musicbrainz.org.

```python
async with anyio.create_task_group() as tg:
    for mbid in mbids:
        tg.start_soon(fetch_one, mb, mbid)  # still ~1 request/second
```

Against a private mirror, raise or disable the limit with `rate_limit=`. To
share one budget between several async clients, create an
[`AsyncRateLimiter`][pymusicbrainz.AsyncRateLimiter] and pass it as
`rate_limiter=` to each.

## Differences from the sync client

There are two:

- `aclose()` instead of `close()`.
- `iter_browse`, `iter_search`, `genres.iter_all` and
  `collections.iter_items` are *not* awaited themselves: they return an async
  iterator straight away, and validate their arguments before returning.
