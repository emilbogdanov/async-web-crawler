"""HTTP fetching with timeouts, bounded retries, Retry-After support and a body size cap."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from crawler.config import Settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
MAX_BACKOFF_SECONDS = 30.0

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    text: str

    @property
    def is_html(self) -> bool:
        return _is_html(self.content_type)


class FetchError(Exception):
    """A URL could not be fetched (network failure after retries, redirect loop, ...)."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{url}: {reason}")
        self.url = url


class ResponseTooLargeError(FetchError):
    """The response body exceeded ``max_response_bytes``."""


def build_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent},
        timeout=httpx.Timeout(settings.request_timeout),
        follow_redirects=True,
        limits=httpx.Limits(
            max_connections=settings.concurrency,
            max_keepalive_connections=settings.concurrency,
        ),
    )


class Fetcher:
    """Fetches pages with retries on transport errors and retryable status codes.

    Non-retryable responses (e.g. 404) are returned, not raised: they are crawl data.
    Bodies are only downloaded for successful HTML responses.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = client
        self._max_retries = settings.max_retries
        self._backoff_base = settings.backoff_base
        self._max_bytes = settings.max_response_bytes
        self._sleep = sleep

    async def fetch(self, url: str) -> FetchResult:
        attempt = 0
        while True:
            try:
                result, retry_after = await self._fetch_once(url)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise FetchError(url, f"{type(exc).__name__}: {exc}") from exc
                delay = self._backoff(attempt)
                reason = type(exc).__name__
            except httpx.HTTPError as exc:
                raise FetchError(url, f"{type(exc).__name__}: {exc}") from exc
            else:
                if result.status_code not in RETRYABLE_STATUSES or attempt >= self._max_retries:
                    return result
                delay = retry_after if retry_after is not None else self._backoff(attempt)
                reason = f"HTTP {result.status_code}"

            attempt += 1
            logger.warning(
                "Retrying %s after %s (attempt %d/%d) in %.2fs",
                url,
                reason,
                attempt,
                self._max_retries,
                delay,
            )
            await self._sleep(delay)

    async def _fetch_once(self, url: str) -> tuple[FetchResult, float | None]:
        async with self._client.stream("GET", url) as response:
            content_type = response.headers.get("content-type", "")
            text = ""
            if response.is_success and _is_html(content_type):
                text = await self._read_text(response)
            result = FetchResult(
                url=url,
                final_url=str(response.url),
                status_code=response.status_code,
                content_type=content_type,
                text=text,
            )
            return result, _parse_retry_after(response.headers.get("retry-after"))

    async def _read_text(self, response: httpx.Response) -> str:
        url = str(response.url)
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self._max_bytes:
            raise ResponseTooLargeError(url, f"declared {declared} bytes > {self._max_bytes}")

        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_bytes:
                raise ResponseTooLargeError(url, f"body exceeds {self._max_bytes} bytes")
            chunks.append(chunk)
        return _decode(b"".join(chunks), response.charset_encoding)

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter."""
        ceiling = min(MAX_BACKOFF_SECONDS, self._backoff_base * 2**attempt)
        return random.uniform(0, ceiling)  # noqa: S311 - jitter, not crypto


def _is_html(content_type: str) -> bool:
    return content_type.split(";", 1)[0].strip().lower() in HTML_CONTENT_TYPES


def _decode(body: bytes, charset: str | None) -> str:
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a delay-seconds Retry-After value; HTTP-date form is ignored."""
    if value is None or not value.strip().isdigit():
        return None
    return min(float(value), MAX_BACKOFF_SECONDS)
