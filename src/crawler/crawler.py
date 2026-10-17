"""Crawl orchestration: bounded worker pool over the frontier."""

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from crawler.config import Settings
from crawler.fetcher import Fetcher, FetchError, Sleep
from crawler.frontier import CrawlTask, Frontier
from crawler.models import PageRecord, PageSink
from crawler.parser import parse_html
from crawler.ratelimit import HostRateLimiter
from crawler.robots import RobotsCache
from crawler.urls import host_of, normalize_url

logger = logging.getLogger(__name__)

BLOCKED_BY_ROBOTS = "blocked by robots.txt"


@dataclass(slots=True)
class CrawlStats:
    pages: int = 0
    errors: int = 0
    blocked: int = 0


class Crawler:
    """Breadth-first crawl with ``settings.concurrency`` workers.

    Expected per-page failures (network errors, oversized bodies, robots blocks) become
    records. Anything else, including a failing sink, aborts the crawl with an
    ``ExceptionGroup`` rather than being silently swallowed.
    """

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._fetcher = Fetcher(client, settings, sleep=sleep)
        self._robots = RobotsCache(client, settings.user_agent) if settings.respect_robots else None
        self._limiter = HostRateLimiter(settings.per_domain_delay, sleep=sleep)

    async def crawl(self, seeds: Iterable[str], sink: PageSink) -> CrawlStats:
        frontier = self._build_frontier(seeds)
        stats = CrawlStats()

        async with asyncio.TaskGroup() as group:
            workers = [
                group.create_task(self._worker(frontier, sink, stats))
                for _ in range(self._settings.concurrency)
            ]
            await frontier.join()
            for worker in workers:
                worker.cancel()

        logger.info(
            "Crawl finished: %d pages, %d errors, %d blocked",
            stats.pages,
            stats.errors,
            stats.blocked,
        )
        return stats

    def _build_frontier(self, seeds: Iterable[str]) -> Frontier:
        normalized: list[str] = []
        for seed in seeds:
            url = normalize_url(seed)
            if url is None:
                raise ValueError(f"Invalid seed URL: {seed!r}")
            normalized.append(url)
        if not normalized:
            raise ValueError("At least one seed URL is required")

        allowed = (
            frozenset(host_of(url) for url in normalized) if self._settings.same_host_only else None
        )
        frontier = Frontier(
            max_depth=self._settings.max_depth,
            max_pages=self._settings.max_pages,
            allowed_hosts=allowed,
        )
        for url in normalized:
            frontier.add(url, 0)
        return frontier

    async def _worker(self, frontier: Frontier, sink: PageSink, stats: CrawlStats) -> None:
        while True:
            task = await frontier.get()
            try:
                record = await self._process(task, frontier)
                await sink(record)
                if record.error == BLOCKED_BY_ROBOTS:
                    stats.blocked += 1
                elif record.error is not None:
                    stats.errors += 1
                else:
                    stats.pages += 1
            finally:
                frontier.task_done()

    async def _process(self, task: CrawlTask, frontier: Frontier) -> PageRecord:
        url, host = task.url, host_of(task.url)

        if self._robots is not None:
            if not await self._robots.can_fetch(url):
                logger.info("Blocked by robots.txt: %s", url)
                return _record(task, error=BLOCKED_BY_ROBOTS)
            self._limiter.set_crawl_delay(host, await self._robots.crawl_delay(url))

        await self._limiter.wait(host)
        try:
            result = await self._fetcher.fetch(url)
        except FetchError as exc:
            logger.warning("Failed %s: %s", url, exc)
            return _record(task, error=str(exc))

        final_url = normalize_url(result.final_url) or url
        if final_url != url:
            frontier.mark_seen(final_url)
            if task.depth == 0:
                frontier.allow_host(host_of(final_url))

        title: str | None = None
        links: tuple[str, ...] = ()
        if result.is_html and result.text:
            page = parse_html(result.text, final_url)
            title, links = page.title, page.links
            for link in links:
                frontier.add(link, task.depth + 1)

        logger.info(
            "Fetched %s [%d] depth=%d links=%d", url, result.status_code, task.depth, len(links)
        )
        return _record(
            task,
            final_url=final_url,
            status_code=result.status_code,
            content_type=result.content_type or None,
            title=title,
            links=links,
        )


def _record(
    task: CrawlTask,
    *,
    final_url: str | None = None,
    status_code: int | None = None,
    content_type: str | None = None,
    title: str | None = None,
    links: tuple[str, ...] = (),
    error: str | None = None,
) -> PageRecord:
    return PageRecord(
        url=task.url,
        final_url=final_url or task.url,
        depth=task.depth,
        fetched_at=datetime.now(UTC),
        status_code=status_code,
        content_type=content_type,
        title=title,
        links=links,
        error=error,
    )
