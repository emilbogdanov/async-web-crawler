"""Per-host politeness delay between request starts."""

import asyncio
import time
from collections.abc import Awaitable, Callable

MAX_CRAWL_DELAY_SECONDS = 30.0

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class HostRateLimiter:
    """Spaces out requests to the same host; different hosts never block each other.

    Each ``wait`` reserves the host's next free slot synchronously (no ``await`` between
    read and write), so concurrent callers get staggered slots without needing a lock.
    """

    def __init__(
        self,
        default_delay: float,
        *,
        max_delay: float = MAX_CRAWL_DELAY_SECONDS,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._default_delay = default_delay
        self._max_delay = max(max_delay, default_delay)
        self._clock = clock
        self._sleep = sleep
        self._delays: dict[str, float] = {}
        self._next_slot: dict[str, float] = {}

    def set_crawl_delay(self, host: str, crawl_delay: float | None) -> None:
        """Apply a robots.txt Crawl-delay: never below the default, never above the cap."""
        if crawl_delay is None:
            return
        self._delays[host] = min(max(crawl_delay, self._default_delay), self._max_delay)

    def delay_for(self, host: str) -> float:
        return self._delays.get(host, self._default_delay)

    async def wait(self, host: str) -> None:
        now = self._clock()
        slot = max(now, self._next_slot.get(host, now))
        self._next_slot[host] = slot + self.delay_for(host)
        if slot > now:
            await self._sleep(slot - now)
