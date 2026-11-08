"""Rate limiters enforcing a minimum interval between requests (MusicBrainz allows ~1 req/s)."""

from __future__ import annotations

import threading
import time

import anyio


class RateLimiter:
    """Thread-safe limiter for the sync client. Share one instance between clients to share a budget."""

    def __init__(self, requests_per_second: float = 1.0):
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self.interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            if (wait := self._next - now) > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next = now + self.interval


class AsyncRateLimiter:
    """Task-safe limiter for the async client (works under asyncio and trio)."""

    def __init__(self, requests_per_second: float = 1.0):
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
                await anyio.sleep(wait)
                now = time.monotonic()
            self._next = now + self.interval
