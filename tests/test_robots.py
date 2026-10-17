import asyncio
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from crawler.robots import ROBOTS_MAX_BYTES, RobotsCache

UA = "AsyncWebCrawler/0.1 (+https://github.com/ESBogdanov25/async-web-crawler)"
ROBOTS_URL = "https://example.com/robots.txt"

RULES = """
User-agent: *
Disallow: /
Allow: /public
Disallow: /public/*.pdf$
Crawl-delay: 5

User-agent: AsyncWebCrawler
Disallow: /private
Crawl-delay: 1.5
"""


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": UA}) as c:
        yield c


@pytest.fixture
def robots(client: httpx.AsyncClient) -> RobotsCache:
    return RobotsCache(client, UA)


def serve(mock: respx.MockRouter, body: str, url: str = ROBOTS_URL) -> respx.Route:
    return mock.get(url).respond(200, text=body, headers={"content-type": "text/plain"})


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/anything", True),
        ("/private", False),
        ("/private/deep", False),
    ],
)
async def test_uses_group_matching_our_user_agent(
    robots: RobotsCache, mock: respx.MockRouter, path: str, allowed: bool
) -> None:
    serve(mock, RULES)
    assert await robots.can_fetch(f"https://example.com{path}") is allowed


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/", False),
        ("/public", True),
        ("/public/page", True),
        ("/public/file.pdf", False),
        ("/public/file.pdf?x=1", True),
    ],
)
async def test_longest_match_and_wildcards(
    client: httpx.AsyncClient, mock: respx.MockRouter, path: str, allowed: bool
) -> None:
    serve(mock, RULES)
    robots = RobotsCache(client, "SomeOtherBot/1.0")
    assert await robots.can_fetch(f"https://example.com{path}") is allowed


async def test_crawl_delay(client: httpx.AsyncClient, mock: respx.MockRouter) -> None:
    serve(mock, RULES)
    assert await RobotsCache(client, UA).crawl_delay("https://example.com/") == 1.5
    assert await RobotsCache(client, "OtherBot").crawl_delay("https://example.com/") == 5.0


async def test_crawl_delay_absent(robots: RobotsCache, mock: respx.MockRouter) -> None:
    serve(mock, "User-agent: *\nDisallow: /x")
    assert await robots.crawl_delay("https://example.com/") is None


async def test_fetches_once_per_origin(robots: RobotsCache, mock: respx.MockRouter) -> None:
    route = serve(mock, RULES)

    for path in ("/a", "/b", "/private", "/c"):
        await robots.can_fetch(f"https://example.com{path}")
    await robots.crawl_delay("https://example.com/")

    assert route.call_count == 1


async def test_concurrent_requests_fetch_once(robots: RobotsCache, mock: respx.MockRouter) -> None:
    route = serve(mock, RULES)

    results = await asyncio.gather(
        *(robots.can_fetch(f"https://example.com/p{i}") for i in range(20))
    )

    assert all(results)
    assert route.call_count == 1


async def test_origins_are_cached_separately(robots: RobotsCache, mock: respx.MockRouter) -> None:
    # respx ignores the port when a pattern omits it, so the port route goes first.
    port = serve(mock, "", url="https://example.com:8443/robots.txt")
    https = serve(mock, "User-agent: *\nDisallow: /")
    http = serve(mock, "", url="http://example.com/robots.txt")

    assert await robots.can_fetch("https://example.com/a") is False
    assert await robots.can_fetch("http://example.com/a") is True
    assert await robots.can_fetch("https://example.com:8443/a") is True
    assert (https.call_count, http.call_count, port.call_count) == (1, 1, 1)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
async def test_client_error_allows_all(
    robots: RobotsCache, mock: respx.MockRouter, status: int
) -> None:
    mock.get(ROBOTS_URL).respond(status)
    assert await robots.can_fetch("https://example.com/anything") is True


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_server_error_or_rate_limit_disallows_all(
    robots: RobotsCache, mock: respx.MockRouter, status: int
) -> None:
    mock.get(ROBOTS_URL).respond(status)
    assert await robots.can_fetch("https://example.com/anything") is False


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("x")],
)
async def test_network_error_disallows_all(
    robots: RobotsCache, mock: respx.MockRouter, error: Exception
) -> None:
    mock.get(ROBOTS_URL).mock(side_effect=error)
    assert await robots.can_fetch("https://example.com/anything") is False


async def test_failure_is_cached_too(robots: RobotsCache, mock: respx.MockRouter) -> None:
    route = mock.get(ROBOTS_URL).respond(503)

    await robots.can_fetch("https://example.com/a")
    await robots.can_fetch("https://example.com/b")

    assert route.call_count == 1


async def test_follows_redirect(robots: RobotsCache, mock: respx.MockRouter) -> None:
    target = "https://www.example.com/robots.txt"
    mock.get(ROBOTS_URL).respond(301, headers={"location": target})
    serve(mock, "User-agent: *\nDisallow: /secret", url=target)

    assert await robots.can_fetch("https://example.com/secret") is False
    assert await robots.can_fetch("https://example.com/open") is True


async def test_redirect_loop_allows_all(robots: RobotsCache, mock: respx.MockRouter) -> None:
    mock.get(ROBOTS_URL).respond(302, headers={"location": ROBOTS_URL})
    assert await robots.can_fetch("https://example.com/anything") is True


async def test_handles_utf8_bom(robots: RobotsCache, mock: respx.MockRouter) -> None:
    body = "﻿User-agent: *\nDisallow: /blocked\n".encode()
    mock.get(ROBOTS_URL).respond(200, content=body)
    assert await robots.can_fetch("https://example.com/blocked") is False


async def test_ignores_content_beyond_size_limit(
    robots: RobotsCache, mock: respx.MockRouter
) -> None:
    padding = "# filler\n" * (ROBOTS_MAX_BYTES // 9 + 10)
    body = f"User-agent: *\nDisallow: /early\n{padding}Disallow: /late\n"
    mock.get(ROBOTS_URL).respond(200, text=body)

    assert await robots.can_fetch("https://example.com/early") is False
    assert await robots.can_fetch("https://example.com/late") is True


async def test_robots_txt_itself_is_always_fetchable(
    robots: RobotsCache, mock: respx.MockRouter
) -> None:
    serve(mock, "User-agent: *\nDisallow: /")
    assert await robots.can_fetch(ROBOTS_URL) is True
