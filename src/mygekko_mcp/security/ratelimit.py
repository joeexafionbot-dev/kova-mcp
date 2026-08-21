"""A small async token-bucket rate limiter to stay under the Plus burst limit.

The Query API returns 429 when the rate/burst limit is exceeded; throttling
client-side keeps us below it and avoids polluting the alarm log with failures.
"""

from __future__ import annotations

import asyncio
import time


class AsyncRateLimiter:
    """Token bucket: up to ``burst`` requests, refilled at ``rate`` per second."""

    def __init__(self, rate_per_sec: float = 5.0, burst: int = 10) -> None:
        self._rate = max(0.01, rate_per_sec)
        self._capacity = max(1, burst)
        self._tokens = float(self._capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                await asyncio.sleep(deficit / self._rate)
