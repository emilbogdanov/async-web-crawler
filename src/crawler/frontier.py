"""Crawl frontier: breadth-first queue with deduplication, depth, page and host limits."""

import asyncio
from dataclasses import dataclass

from crawler.urls import host_of


@dataclass(frozen=True, slots=True)
class CrawlTask:
    url: str
    depth: int


class Frontier:
    """FIFO queue of URLs to crawl. URLs passed in must already be normalized.

    Memory is bounded by ``max_pages``: once that many URLs have been scheduled,
    further ``add`` calls are rejected without touching the queue.
    """

    def __init__(
        self,
        *,
        max_depth: int,
        max_pages: int,
        allowed_hosts: frozenset[str] | None = None,
    ) -> None:
        self._max_depth = max_depth
        self._max_pages = max_pages
        self._allowed_hosts = set(allowed_hosts) if allowed_hosts is not None else None
        self._seen: set[str] = set()
        self._scheduled = 0
        self._queue: asyncio.Queue[CrawlTask] = asyncio.Queue()

    @property
    def scheduled(self) -> int:
        return self._scheduled

    def add(self, url: str, depth: int) -> bool:
        """Schedule ``url`` if it passes every limit. Returns whether it was queued."""
        if (
            depth > self._max_depth
            or self._scheduled >= self._max_pages
            or url in self._seen
            or (self._allowed_hosts is not None and host_of(url) not in self._allowed_hosts)
        ):
            return False
        self._seen.add(url)
        self._scheduled += 1
        self._queue.put_nowait(CrawlTask(url, depth))
        return True

    def allow_host(self, host: str) -> None:
        """Extend the host allow-list, e.g. when a seed redirects to ``www.``."""
        if self._allowed_hosts is not None:
            self._allowed_hosts.add(host)

    def mark_seen(self, url: str) -> None:
        """Record an alias (e.g. a redirect target) so it is never scheduled separately."""
        self._seen.add(url)

    async def get(self) -> CrawlTask:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    async def join(self) -> None:
        """Wait until every scheduled task has been marked done."""
        await self._queue.join()
