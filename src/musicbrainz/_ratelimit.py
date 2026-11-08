"""Rate limiters enforcing a minimum interval between requests.

MusicBrainz allows about one request per second per client.
"""

import logging
import threading
import time

import anyio

logger = logging.getLogger(__name__)

#: Waits shorter than this are not worth a log line.
_LOG_WAIT_THRESHOLD = 0.01


def _log_wait(wait: float) -> None:
    if wait >= _LOG_WAIT_THRESHOLD:
        logger.debug("rate limit: waiting %.2fs", wait)


class RateLimiter:
    """Thread-safe limiter for the sync client.

    Pass one instance to several clients to make them share a budget.
    """

    def __init__(self, requests_per_second: float = 1.0) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self.interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            if (wait := self._next - now) > 0:
                _log_wait(wait)
                time.sleep(wait)
                now = time.monotonic()
            self._next = now + self.interval


class AsyncRateLimiter:
    """Task-safe limiter for the async client (works under asyncio and trio)."""

    def __init__(self, requests_per_second: float = 1.0) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self.interval = 1.0 / requests_per_second
        self._lock: anyio.Lock | None = None
        self._next = 0.0

    async def acquire(self) -> None:
        if self._lock is None:
            self._lock = anyio.Lock()
        async with self._lock:
            now = time.monotonic()
            if (wait := self._next - now) > 0:
                _log_wait(wait)
                await anyio.sleep(wait)
                now = time.monotonic()
            self._next = now + self.interval
