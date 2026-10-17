"""Crawl output records shared by the crawler and exporters."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PageRecord:
    url: str
    final_url: str
    depth: int
    fetched_at: datetime
    status_code: int | None = None
    content_type: str | None = None
    title: str | None = None
    links: tuple[str, ...] = ()
    error: str | None = None


PageSink = Callable[[PageRecord], Awaitable[None]]
