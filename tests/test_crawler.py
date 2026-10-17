from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import respx

from crawler.config import Settings
from crawler.crawler import BLOCKED_BY_ROBOTS, Crawler, CrawlStats
from crawler.fetcher import build_client
from crawler.models import PageRecord

SITE = "https://site.com"


def html(*hrefs: str, title: str = "Page") -> str:
    links = "".join(f'<a href="{href}">x</a>' for href in hrefs)
    return f"<html><head><title>{title}</title></head><body>{links}</body></html>"


def page(mock: respx.MockRouter, url: str, *hrefs: str, title: str = "Page") -> respx.Route:
    return mock.get(url).respond(
        200, headers={"content-type": "text/html"}, text=html(*hrefs, title=title)
    )


class Collector:
    def __init__(self) -> None:
        self.records: list[PageRecord] = []

    async def __call__(self, record: PageRecord) -> None:
        self.records.append(record)

    @property
    def urls(self) -> set[str]:
        return {r.url for r in self.records}

    def by_url(self, url: str) -> PageRecord:
        return next(r for r in self.records if r.url == url)


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "max_depth": 5,
        "max_pages": 50,
        "concurrency": 4,
        "per_domain_delay": 0.0,
        "max_retries": 0,
        "respect_robots": False,
        "same_host_only": True,
    }
    return Settings(_env_file=None, **(defaults | overrides))


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def sink() -> Collector:
    return Collector()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with build_client(make_settings()) as c:
        yield c


async def run(
    client: httpx.AsyncClient,
    sink: Collector,
    seeds: list[str] | None = None,
    sleep: SleepRecorder | None = None,
    **overrides: Any,
) -> CrawlStats:
    crawler = Crawler(make_settings(**overrides), client, sleep=sleep or SleepRecorder())
    return await crawler.crawl(seeds or [SITE], sink)


async def test_crawls_linked_pages_breadth_first(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    page(mock, f"{SITE}/", "/a", "/b", title="Home")
    page(mock, f"{SITE}/a", "/a/1")
    page(mock, f"{SITE}/b")
    page(mock, f"{SITE}/a/1")

    stats = await run(client, sink)

    assert stats == CrawlStats(pages=4, errors=0, blocked=0)
    depths = {r.url: r.depth for r in sink.records}
    assert depths == {f"{SITE}/": 0, f"{SITE}/a": 1, f"{SITE}/b": 1, f"{SITE}/a/1": 2}
    home = sink.by_url(f"{SITE}/")
    assert home.title == "Home"
    assert home.status_code == 200
    assert home.links == (f"{SITE}/a", f"{SITE}/b")


async def test_respects_max_depth(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    page(mock, f"{SITE}/", "/a")
    page(mock, f"{SITE}/a", "/a/1")
    deep = page(mock, f"{SITE}/a/1")

    await run(client, sink, max_depth=1)

    assert sink.urls == {f"{SITE}/", f"{SITE}/a"}
    assert deep.call_count == 0


async def test_respects_max_pages(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    page(mock, f"{SITE}/", *(f"/p{i}" for i in range(10)))
    mock.get(url__regex=rf"{SITE}/p\d+").respond(200, headers={"content-type": "text/html"})

    stats = await run(client, sink, max_pages=4)

    assert stats.pages == 4
    assert len(sink.records) == 4


async def test_fetches_each_url_once(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    page(mock, f"{SITE}/", "/a", "/b")
    page(mock, f"{SITE}/a", "/", "/b", "/b#frag")
    shared = page(mock, f"{SITE}/b", "/a", "/")

    await run(client, sink)

    assert len(sink.records) == 3
    assert shared.call_count == 1


async def test_same_host_only_skips_external_links(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    page(mock, f"{SITE}/", "https://other.com/x")
    external = page(mock, "https://other.com/x")

    await run(client, sink)

    assert sink.urls == {f"{SITE}/"}
    assert external.call_count == 0


async def test_follows_external_links_when_allowed(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    page(mock, f"{SITE}/", "https://other.com/x")
    page(mock, "https://other.com/x")

    await run(client, sink, same_host_only=False)

    assert sink.urls == {f"{SITE}/", "https://other.com/x"}


async def test_seed_redirect_to_new_host_is_followed(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    mock.get(f"{SITE}/").respond(301, headers={"location": "https://www.site.com/"})
    page(mock, "https://www.site.com/", "/about")
    page(mock, "https://www.site.com/about")

    await run(client, sink)

    assert sink.urls == {f"{SITE}/", "https://www.site.com/about"}
    assert sink.by_url(f"{SITE}/").final_url == "https://www.site.com/"


async def test_redirect_target_is_not_fetched_again(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    page(mock, f"{SITE}/", "/old")
    mock.get(f"{SITE}/old").respond(301, headers={"location": "/new"})
    target = page(mock, f"{SITE}/new", "/new")

    await run(client, sink, concurrency=1)

    assert sink.urls == {f"{SITE}/", f"{SITE}/old"}
    assert target.call_count == 1


async def test_records_fetch_errors_and_continues(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    page(mock, f"{SITE}/", "/down", "/ok")
    mock.get(f"{SITE}/down").mock(side_effect=httpx.ConnectError("refused"))
    page(mock, f"{SITE}/ok")

    stats = await run(client, sink)

    assert stats == CrawlStats(pages=2, errors=1, blocked=0)
    down = sink.by_url(f"{SITE}/down")
    assert down.status_code is None
    assert down.error is not None
    assert "ConnectError" in down.error


async def test_http_errors_are_pages_not_errors(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    page(mock, f"{SITE}/", "/missing")
    mock.get(f"{SITE}/missing").respond(404, headers={"content-type": "text/html"})

    stats = await run(client, sink)

    assert stats.pages == 2
    assert sink.by_url(f"{SITE}/missing").status_code == 404


async def test_non_html_is_recorded_but_not_parsed(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    page(mock, f"{SITE}/", "/file.pdf")
    mock.get(f"{SITE}/file.pdf").respond(200, headers={"content-type": "application/pdf"})

    await run(client, sink)

    pdf = sink.by_url(f"{SITE}/file.pdf")
    assert pdf.content_type == "application/pdf"
    assert pdf.links == ()
    assert pdf.title is None


async def test_robots_disallowed_pages_are_blocked(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    mock.get(f"{SITE}/robots.txt").respond(200, text="User-agent: *\nDisallow: /private")
    page(mock, f"{SITE}/", "/private", "/public")
    private = page(mock, f"{SITE}/private")
    page(mock, f"{SITE}/public")

    stats = await run(client, sink, respect_robots=True)

    assert stats == CrawlStats(pages=2, errors=0, blocked=1)
    assert sink.by_url(f"{SITE}/private").error == BLOCKED_BY_ROBOTS
    assert private.call_count == 0


async def test_robots_ignored_when_disabled(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    robots = mock.get(f"{SITE}/robots.txt").respond(200, text="User-agent: *\nDisallow: /")
    page(mock, f"{SITE}/")

    await run(client, sink, respect_robots=False)

    assert robots.call_count == 0
    assert sink.urls == {f"{SITE}/"}


async def test_applies_crawl_delay(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    mock.get(f"{SITE}/robots.txt").respond(200, text="User-agent: *\nCrawl-delay: 2")
    page(mock, f"{SITE}/", "/a")
    page(mock, f"{SITE}/a")
    sleep = SleepRecorder()

    await run(client, sink, sleep=sleep, respect_robots=True, concurrency=1)

    assert sleep.delays == [pytest.approx(2.0, abs=0.1)]


@pytest.mark.parametrize("seeds", [["not a url"], ["mailto:x@y.z"], []])
async def test_rejects_invalid_seeds(client: httpx.AsyncClient, seeds: list[str]) -> None:
    with pytest.raises(ValueError, match="seed"):
        await Crawler(make_settings(), client).crawl(seeds, Collector())


async def test_multiple_seeds_on_different_hosts(
    client: httpx.AsyncClient, mock: respx.MockRouter, sink: Collector
) -> None:
    page(mock, "https://one.com/", "https://two.com/x")
    page(mock, "https://two.com/", "/y")
    page(mock, "https://two.com/x")
    page(mock, "https://two.com/y")

    await run(client, sink, seeds=["https://one.com", "https://two.com"])

    assert sink.urls == {
        "https://one.com/",
        "https://two.com/",
        "https://two.com/x",
        "https://two.com/y",
    }


async def test_sink_failure_aborts_crawl(client: httpx.AsyncClient, mock: respx.MockRouter) -> None:
    page(mock, f"{SITE}/", "/a")
    page(mock, f"{SITE}/a")

    async def failing_sink(record: PageRecord) -> None:
        raise OSError("disk full")

    crawler = Crawler(make_settings(), client)
    with pytest.raises(ExceptionGroup) as exc_info:
        await crawler.crawl([SITE], failing_sink)

    assert exc_info.group_contains(OSError, match="disk full")
