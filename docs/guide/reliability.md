# Rate limiting, errors & logging

## Rate limiting

MusicBrainz allows about
[one request per second](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting)
per client, and may block IP addresses that send more. The client enforces
this by default:

```python
MusicBrainz("MyApp", "1.0")  # 1 request/second
MusicBrainz("MyApp", "1.0", rate_limit=None)  # no limit, e.g. for a mirror
MusicBrainz("MyApp", "1.0", base_url="https://mb.example.org/ws/2", rate_limit=10)
```

Each client has its own limiter. If one process uses several clients, give
them a shared one so that together they stay within the limit:

```python
from pymusicbrainz import RateLimiter

limiter = RateLimiter(requests_per_second=1)
a = MusicBrainz("MyApp", "1.0", rate_limiter=limiter)
b = MusicBrainz("MyApp", "1.0", rate_limiter=limiter)
```

`RateLimiter` is thread-safe. Use
[`AsyncRateLimiter`][pymusicbrainz.AsyncRateLimiter] for async clients.

## Proxies

By default the client uses the `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and
`NO_PROXY` environment variables, like most HTTP tools. To set a proxy in code
instead, pass `proxy=`:

```python
MusicBrainz("MyApp", "1.0", proxy="http://proxy.example.org:3128")
MusicBrainz("MyApp", "1.0", proxy="socks5://localhost:1080")
```

SOCKS proxies need the `socks` extra: `pip install 'pymusicbrainz[socks]'`.
The proxy is used for every request, including Cover Art Archive downloads.
For anything more involved, such as different proxies per host, pass your own
`httpx2` transport as `transport=`.

## Retries

These are retried automatically, up to `max_retries` times (default 3):

- HTTP 429, 502, 503 and 504 responses. MusicBrainz answers 503 when you are
  over the rate limit.
- Network errors such as timeouts and refused connections.

The wait honours the server's `Retry-After` header when it gives a number of
seconds; otherwise it is `backoff_factor * 2 ** attempt` (1, 2, 4 … seconds by
default). Either way it is capped at 60 seconds.

!!! note "Submissions are not replayed"

    A submission (tags, ratings, barcodes, ISRCs, collection changes) is
    retried only if the connection could not be made at all. After a timeout
    or a dropped connection, the server may already have applied it, so the
    error is raised instead.

## Errors

Every exception derives from
[`MusicBrainzError`][pymusicbrainz.errors.MusicBrainzError]:

| Exception | Raised when |
|---|---|
| `ValidationError` | An argument is invalid. Raised before any request; also a `ValueError`. |
| `AuthenticationRequiredError` | The call needs credentials and the client has none. |
| `NetworkError` | The request failed at the transport level, after retries. |
| `ResponseError` | The server returned an error status. Base class of the following. |
| ↳ `BadRequestError` | 400, e.g. an include the server rejects. |
| ↳ `AuthenticationError` | 401, credentials missing or wrong. |
| ↳ `ForbiddenError` | 403. |
| ↳ `NotFoundError` | 404, no such entity. |
| ↳ `RateLimitError` | 429 or 503, still failing after the retries. |
| ↳ `ServerError` | Any other 5xx. |

A `ResponseError` has `status_code`, the server's `message`, its `help` text
if any, and the request `url`:

```python
from pymusicbrainz import NotFoundError

try:
    mb.artists.get(mbid)
except NotFoundError as exc:
    print(exc.status_code, exc.message)
```

## Logging

The library logs through the standard `logging` module, under the
`pymusicbrainz` logger. Nothing is printed unless your application configures
logging.

| Logger | Level | Message |
|---|---|---|
| `pymusicbrainz.client` | `DEBUG` | Every HTTP attempt: method, full URL, status and time taken. |
| `pymusicbrainz.client` | `WARNING` | Every retry: what failed, the attempt number and the delay. |
| `pymusicbrainz._ratelimit` | `DEBUG` | Rate-limiter waits of 10 ms or more. |

Headers (including `Authorization`) and response bodies are never logged.

```python
import logging

logging.basicConfig(level=logging.WARNING)  # see retries
logging.getLogger("pymusicbrainz").setLevel(logging.DEBUG)  # see every request
```

The [CLI](../cli.md) turns this on with `-v` and `-vv`.
