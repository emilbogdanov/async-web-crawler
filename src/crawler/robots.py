"""robots.txt fetching and per-origin caching, following RFC 9309 status semantics."""

import asyncio
import logging

import httpx
from protego import Protego

from crawler.urls import origin_of

logger = logging.getLogger(__name__)

ROBOTS_MAX_BYTES = 500 * 1024  # RFC 9309: parse at least the first 500 KiB

_ALLOW_ALL = Protego.parse("")
_DISALLOW_ALL = Protego.parse("User-agent: *\nDisallow: /")


class RobotsCache:
    """Answers robots.txt questions, fetching each origin's file exactly once.

    - 2xx: rules are parsed and applied.
    - 4xx (except 429), or too many redirects: treated as "no robots.txt" -> allow all.
    - 429, 5xx, network errors: treated as "unreachable" -> disallow all for the crawl.
    """

    def __init__(self, client: httpx.AsyncClient, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._policies: dict[str, Protego] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def can_fetch(self, url: str) -> bool:
        policy = await self._policy_for(url)
        return policy.can_fetch(url, self._user_agent)

    async def crawl_delay(self, url: str) -> float | None:
        policy = await self._policy_for(url)
        return policy.crawl_delay(self._user_agent)

    async def _policy_for(self, url: str) -> Protego:
        origin = origin_of(url)
        if (policy := self._policies.get(origin)) is not None:
            return policy

        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if (policy := self._policies.get(origin)) is None:
                policy = await self._load(origin)
                self._policies[origin] = policy
        self._locks.pop(origin, None)
        return policy

    async def _load(self, origin: str) -> Protego:
        robots_url = f"{origin}/robots.txt"
        try:
            async with self._client.stream("GET", robots_url) as response:
                if response.is_success:
                    return Protego.parse(await _read_capped(response))
                status = response.status_code
        except httpx.TooManyRedirects:
            logger.info("robots.txt at %s redirects too often; allowing all", robots_url)
            return _ALLOW_ALL
        except httpx.HTTPError as exc:
            logger.warning(
                "robots.txt at %s unreachable (%s); disallowing origin",
                robots_url,
                type(exc).__name__,
            )
            return _DISALLOW_ALL

        if 400 <= status < 500 and status != httpx.codes.TOO_MANY_REQUESTS:
            logger.debug("No robots.txt at %s (HTTP %d); allowing all", robots_url, status)
            return _ALLOW_ALL
        logger.warning("robots.txt at %s returned HTTP %d; disallowing origin", robots_url, status)
        return _DISALLOW_ALL


async def _read_capped(response: httpx.Response) -> str:
    """Read at most ``ROBOTS_MAX_BYTES``, dropping a trailing partial line if truncated."""
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer += chunk
        if len(buffer) >= ROBOTS_MAX_BYTES:
            del buffer[ROBOTS_MAX_BYTES:]
            del buffer[buffer.rfind(b"\n") + 1 :]
            break
    return buffer.decode("utf-8-sig", errors="replace")
