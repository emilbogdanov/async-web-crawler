from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from crawler.config import Settings
from crawler.fetcher import (
    MAX_BACKOFF_SECONDS,
    Fetcher,
    FetchError,
    ResponseTooLargeError,
    build_client,
)

URL = "https://example.com/page"
HTML = {"content-type": "text/html; charset=utf-8"}


class ChunkedStream(httpx.AsyncByteStream):
    """Body without Content-Length, delivered in chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, max_retries=3, backoff_base=0.5, max_response_bytes=1024)


@pytest.fixture
def sleep() -> SleepRecorder:
    return SleepRecorder()


@pytest.fixture
async def fetcher(settings: Settings, sleep: SleepRecorder) -> AsyncIterator[Fetcher]:
    async with build_client(settings) as client:
        yield Fetcher(client, settings, sleep=sleep)


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


async def test_returns_html_body(fetcher: Fetcher, mock: respx.MockRouter) -> None:
    mock.get(URL).respond(200, headers=HTML, text="<html>hi</html>")

    result = await fetcher.fetch(URL)

    assert result.status_code == 200
    assert result.is_html
    assert result.text == "<html>hi</html>"
    assert result.final_url == URL


async def test_sends_user_agent(
    fetcher: Fetcher, mock: respx.MockRouter, settings: Settings
) -> None:
    route = mock.get(URL).respond(200, headers=HTML, text="")

    await fetcher.fetch(URL)

    assert route.calls.last.request.headers["user-agent"] == settings.user_agent


async def test_follows_redirects(fetcher: Fetcher, mock: respx.MockRouter) -> None:
    target = "https://example.com/new"
    mock.get(URL).respond(301, headers={"location": target})
    mock.get(target).respond(200, headers=HTML, text="moved")

    result = await fetcher.fetch(URL)

    assert result.url == URL
    assert result.final_url == target
    assert result.text == "moved"


async def test_skips_body_for_non_html(fetcher: Fetcher, mock: respx.MockRouter) -> None:
    mock.get(URL).respond(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    result = await fetcher.fetch(URL)

    assert not result.is_html
    assert result.text == ""


@pytest.mark.parametrize("status", [404, 403, 410])
async def test_client_errors_are_returned_without_retry(
    fetcher: Fetcher, mock: respx.MockRouter, sleep: SleepRecorder, status: int
) -> None:
    route = mock.get(URL).respond(status, headers=HTML, text="nope")

    result = await fetcher.fetch(URL)

    assert result.status_code == status
    assert result.text == ""
    assert route.call_count == 1
    assert sleep.delays == []


async def test_retries_server_error_then_succeeds(
    fetcher: Fetcher, mock: respx.MockRouter, sleep: SleepRecorder
) -> None:
    route = mock.get(URL)
    route.side_effect = [httpx.Response(503), httpx.Response(200, headers=HTML, text="ok")]

    result = await fetcher.fetch(URL)

    assert result.status_code == 200
    assert route.call_count == 2
    assert len(sleep.delays) == 1


async def test_returns_last_response_when_retries_exhausted(
    fetcher: Fetcher, mock: respx.MockRouter, sleep: SleepRecorder
) -> None:
    route = mock.get(URL).respond(502)

    result = await fetcher.fetch(URL)

    assert result.status_code == 502
    assert route.call_count == 4
    assert len(sleep.delays) == 3


async def test_backoff_is_bounded_exponential(
    fetcher: Fetcher, mock: respx.MockRouter, sleep: SleepRecorder
) -> None:
    mock.get(URL).respond(500)

    await fetcher.fetch(URL)

    for attempt, delay in enumerate(sleep.delays):
        assert 0 <= delay <= 0.5 * 2**attempt


async def test_honors_retry_after(
    fetcher: Fetcher, mock: respx.MockRouter, sleep: SleepRecorder
) -> None:
    mock.get(URL).side_effect = [
        httpx.Response(429, headers={"retry-after": "7"}),
        httpx.Response(200, headers=HTML, text="ok"),
    ]

    await fetcher.fetch(URL)

    assert sleep.delays == [7.0]


@pytest.mark.parametrize(
    ("header", "expected_max"),
    [("3600", MAX_BACKOFF_SECONDS), ("Wed, 21 Oct 2026 07:28:00 GMT", 0.5), ("-5", 0.5)],
)
async def test_retry_after_is_capped_or_ignored(
    fetcher: Fetcher,
    mock: respx.MockRouter,
    sleep: SleepRecorder,
    header: str,
    expected_max: float,
) -> None:
    mock.get(URL).side_effect = [
        httpx.Response(503, headers={"retry-after": header}),
        httpx.Response(200, headers=HTML, text="ok"),
    ]

    await fetcher.fetch(URL)

    assert len(sleep.delays) == 1
    assert sleep.delays[0] <= expected_max


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("x")],
)
async def test_retries_transport_errors(
    fetcher: Fetcher, mock: respx.MockRouter, error: httpx.TransportError
) -> None:
    route = mock.get(URL)
    route.side_effect = [error, httpx.Response(200, headers=HTML, text="ok")]

    result = await fetcher.fetch(URL)

    assert result.text == "ok"
    assert route.call_count == 2


async def test_raises_after_transport_retries_exhausted(
    fetcher: Fetcher, mock: respx.MockRouter
) -> None:
    route = mock.get(URL).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(FetchError, match="ConnectError") as exc_info:
        await fetcher.fetch(URL)

    assert exc_info.value.url == URL
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
    assert route.call_count == 4


async def test_no_retries_when_disabled(
    settings: Settings, mock: respx.MockRouter, sleep: SleepRecorder
) -> None:
    no_retry = settings.model_copy(update={"max_retries": 0})
    route = mock.get(URL).respond(503)

    async with build_client(no_retry) as client:
        result = await Fetcher(client, no_retry, sleep=sleep).fetch(URL)

    assert result.status_code == 503
    assert route.call_count == 1


async def test_redirect_loop_raises_without_retry(
    fetcher: Fetcher, mock: respx.MockRouter, sleep: SleepRecorder
) -> None:
    mock.get(URL).respond(302, headers={"location": URL})

    with pytest.raises(FetchError, match="TooManyRedirects"):
        await fetcher.fetch(URL)

    assert sleep.delays == []


async def test_rejects_large_declared_body(
    fetcher: Fetcher, mock: respx.MockRouter, sleep: SleepRecorder
) -> None:
    route = mock.get(URL).respond(200, headers=HTML, content=b"x" * 2048)

    with pytest.raises(ResponseTooLargeError):
        await fetcher.fetch(URL)

    assert route.call_count == 1
    assert sleep.delays == []


async def test_rejects_large_streamed_body(fetcher: Fetcher, mock: respx.MockRouter) -> None:
    stream = ChunkedStream([b"x" * 600, b"x" * 600])
    mock.get(URL).respond(200, headers=HTML, stream=stream)

    with pytest.raises(ResponseTooLargeError, match="exceeds"):
        await fetcher.fetch(URL)


async def test_accepts_body_at_exact_limit(fetcher: Fetcher, mock: respx.MockRouter) -> None:
    mock.get(URL).respond(200, headers=HTML, stream=ChunkedStream([b"x" * 1024]))

    result = await fetcher.fetch(URL)

    assert len(result.text) == 1024


async def test_decodes_declared_charset(fetcher: Fetcher, mock: respx.MockRouter) -> None:
    body = "Grüße".encode("iso-8859-1")
    mock.get(URL).respond(
        200, headers={"content-type": "text/html; charset=iso-8859-1"}, content=body
    )

    result = await fetcher.fetch(URL)

    assert result.text == "Grüße"


async def test_unknown_charset_falls_back_to_utf8(fetcher: Fetcher, mock: respx.MockRouter) -> None:
    body = "Здравей".encode()
    mock.get(URL).respond(200, headers={"content-type": "text/html; charset=bogus"}, content=body)

    result = await fetcher.fetch(URL)

    assert result.text == "Здравей"


async def test_invalid_bytes_are_replaced(fetcher: Fetcher, mock: respx.MockRouter) -> None:
    mock.get(URL).respond(200, headers=HTML, content=b"ok\xff")

    result = await fetcher.fetch(URL)

    assert result.text == "ok�"
