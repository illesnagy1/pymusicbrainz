# musicbrainz

A typed Python client for the [MusicBrainz WS/2 API](https://musicbrainz.org/doc/MusicBrainz_API).
It has a sync client and an async client (asyncio and trio), built on `httpx2` and `pydantic`.

- Lookup, browse and search for every entity, plus disc ID / ISRC / ISWC / URL lookups
- Typed models for every entity. Unknown fields are kept, not dropped.
- Validates includes, release type/status filters, MBIDs, ISRCs, ISWCs and GTIN checksums before sending
- Built-in rate limiting (1 request/second by default) and retries on 429/502/503/504 and network errors
- Iterators that fetch pages for you, following the API's paging rules for releases
- Digest auth (username plus application token) or OAuth2 bearer tokens
- Submits tags, ratings, barcodes and ISRCs; manages collections
- Helpers for building Lucene queries, and a JSON CLI

## Quick start

```python
from musicbrainz import MusicBrainz

with MusicBrainz("MyApp", "1.0", "me@example.com") as mb:
    nirvana = mb.artists.search(artist="Nirvana", country="US", limit=1)[0]

    artist = mb.artists.get(nirvana.id, inc=["aliases", "url-rels", "genres"])
    for rel in artist.relations_of("url"):
        print(rel.type, rel.url.resource)

    for rg in mb.release_groups.iter_browse(artist=artist.id, release_type="album"):
        print(rg.first_release_date, rg.title)
```

The first three arguments build the `User-Agent`, which MusicBrainz requires: your application name, its version, and a contact URL or e-mail.

### Async

```python
from musicbrainz import AsyncMusicBrainz

async with AsyncMusicBrainz("MyApp", "1.0", "me@example.com") as mb:
    page = await mb.releases.search("Nevermind", status="official")
    async for rec in mb.recordings.iter_browse(release=page[0].id):
        print(rec.title)
```

## API overview

Each entity has a namespace: `areas`, `artists`, `events`, `genres`, `instruments`, `labels`,
`places`, `recordings`, `releases`, `release_groups`, `series`, `works`, `urls` and `collections`.

| Call | Request |
|---|---|
| `mb.<entity>.get(mbid, inc=..., release_type=..., release_status=...)` | lookup |
| `mb.<entity>.browse(<linked>=mbid, inc=..., limit=..., offset=...)` | browse (returns one `Page`) |
| `mb.<entity>.iter_browse(<linked>=mbid, max_items=...)` | browse, all pages |
| `mb.<entity>.search(query, limit=..., dismax=..., **fields)` | search (returns one `Page`) |
| `mb.<entity>.iter_search(query, max_items=..., **fields)` | search, all pages |
| `mb.annotations` / `mb.cdstubs` / `mb.tags` `.search(...)` | search-only indexes |
| `mb.lookup_discid(discid, toc=..., cdstubs=..., all_media_formats=...)` | `/discid` (returns `Disc` or `CDStub`) |
| `mb.lookup_isrc(isrc)` / `mb.lookup_iswc(iswc)` | `/isrc`, `/iswc` |
| `mb.urls.by_resource(url)` / `mb.urls.by_resources([...])` | `/url?resource=` (batched by 100) |
| `mb.genres.all()` / `iter_all()` / `names()` | `/genre/all` |
| `mb.request(method, path, params)` | raw JSON request, for anything else |

**Includes** are passed as a list or a `+`/space-separated string: `inc=["releases", "artist-rels"]`.
If an include doesn't apply to the entity, you get `ValidationError` before any request is sent.
Pass `validate=False` to turn this off, for example to use an include the API added after this library was written.

**Browse links** are keyword arguments. Use `release_group=` for `release-group`; `track_artist=` stays as it is.

**Search:** `query` is raw Lucene. Keyword fields are added as `field:"value"` terms joined with AND.
A list becomes an OR group. `musicbrainz.query.escape()` and `build_query()` are available separately.

**Pages:** a `Page` has `items`, `count`, `offset`, `next_offset` and `has_more`, and supports iteration and indexing.

**Models** use snake_case attributes (`sort_name`, `life_span`, `artist_credit`).
Fields the models don't cover are kept and can be read with `model.model_extra`.
Useful helpers include `Entity.relations_of(target_type, relation_type)`, `Relation.target`, `Release.artist_credit_phrase` and `Collection.item_count`.

## Authentication and submissions

```python
mb = MusicBrainz(
    "MyApp",
    "1.0",
    "me@example.com",
    username="me",
    password="<token from musicbrainz.org/account/applications>",
)
# or: oauth_token="..."

mb.submit_tags("artist", {artist_id: ["grunge", "rock"]}, vote="upvote")
mb.submit_ratings("recording", {recording_id: 80})  # 0-100, 20 per star
mb.submit_barcodes({release_id: "4006381333931"})  # checksum validated
mb.submit_isrcs({recording_id: ["USGF19942501"]})

mb.collections.add(collection_id, "release", release_ids)  # batched by 400
mb.collections.remove(collection_id, "release", release_ids)
mb.collections.of_editor("me", include_private=True)
mb.collections.iter_items(collection_id, "release")
```

Submissions send `client=<app>-<version>`. The API forbids hyphens in the version, so use a version without one.
`user-*` includes and submissions raise `AuthenticationRequiredError` if no credentials are configured.

## Rate limiting, retries and errors

- `rate_limit=1.0` is in requests per second. Use `None` for a private mirror (`base_url=...`).
  To share one budget across several clients, pass a single `RateLimiter` or `AsyncRateLimiter` to each.
- `max_retries=3`, with exponential backoff that honours `Retry-After`.
  A submission is never retried if the request may already have reached the server.
- Errors subclass `MusicBrainzError`:
  - `ValidationError`
  - `AuthenticationRequiredError`
  - `NetworkError`
  - `ResponseError`, with subclasses `BadRequestError`, `AuthenticationError`, `ForbiddenError`, `NotFoundError`, `RateLimitError` and `ServerError`. Each carries `status_code`, `message` and `help`.

## CLI

```sh
musicbrainz search artist "Daft Punk" --limit 3
musicbrainz lookup release-group 1b022e01-4da6-387b-8658-8678046e4cef --inc releases --type album
musicbrainz browse release --artist 5b11f4ce-a62d-471e-81fc-a69a8278c7da --status official --all
musicbrainz isrc USGF19942501
musicbrainz url https://www.discogs.com/artist/125246
musicbrainz genres
```

Set `MUSICBRAINZ_APP`, `MUSICBRAINZ_CONTACT` and `MUSICBRAINZ_URL` to configure it.

## Development

```sh
uv run pytest
```

The tests run offline using `httpx2.MockTransport`.

## Design

`_core.py` is sans-IO. It turns each method call into a request description (method, path, params, body)
plus a parser, and holds all validation and error mapping. `MusicBrainz` and `AsyncMusicBrainz` only send requests, rate-limit and retry.
This keeps the two clients identical in behaviour.
