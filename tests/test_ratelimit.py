import asyncio
import time

import pytest

from crawler.ratelimit import MAX_CRAWL_DELAY_SECONDS, HostRateLimiter


class FakeTime:
    """Manual clock; ``sleep`` records requests and optionally advances time."""

    def __init__(self, *, advance_on_sleep: bool = True) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []
        self._advance = advance_on_sleep

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        if self._advance:
            self.now += delay


def make_limiter(fake: FakeTime, delay: float = 1.0) -> HostRateLimiter:
    return HostRateLimiter(delay, clock=fake.clock, sleep=fake.sleep)


async def test_first_request_does_not_wait() -> None:
    fake = FakeTime()
    await make_limiter(fake).wait("a.com")
    assert fake.sleeps == []


async def test_second_request_waits_full_delay() -> None:
    fake = FakeTime()
    limiter = make_limiter(fake)

    await limiter.wait("a.com")
    await limiter.wait("a.com")

    assert fake.sleeps == [1.0]


async def test_waits_only_the_remaining_time() -> None:
    fake = FakeTime()
    limiter = make_limiter(fake)

    await limiter.wait("a.com")
    fake.now += 0.75
    await limiter.wait("a.com")

    assert fake.sleeps == [pytest.approx(0.25)]


async def test_no_wait_after_delay_elapsed() -> None:
    fake = FakeTime()
    limiter = make_limiter(fake)

    await limiter.wait("a.com")
    fake.now += 5
    await limiter.wait("a.com")

    assert fake.sleeps == []


async def test_hosts_are_independent() -> None:
    fake = FakeTime()
    limiter = make_limiter(fake)

    for host in ("a.com", "b.com", "c.com:8080"):
        await limiter.wait(host)

    assert fake.sleeps == []


async def test_concurrent_waiters_get_staggered_slots() -> None:
    fake = FakeTime(advance_on_sleep=False)
    limiter = make_limiter(fake)

    await asyncio.gather(*(limiter.wait("a.com") for _ in range(4)))

    assert fake.sleeps == [1.0, 2.0, 3.0]


async def test_zero_delay_never_sleeps() -> None:
    fake = FakeTime()
    limiter = make_limiter(fake, delay=0.0)

    await asyncio.gather(*(limiter.wait("a.com") for _ in range(5)))

    assert fake.sleeps == []


@pytest.mark.parametrize(
    ("crawl_delay", "expected"),
    [
        (None, 1.0),
        (5.0, 5.0),
        (0.2, 1.0),
        (-3.0, 1.0),
        (3600.0, MAX_CRAWL_DELAY_SECONDS),
    ],
)
def test_crawl_delay_is_clamped(crawl_delay: float | None, expected: float) -> None:
    limiter = HostRateLimiter(1.0)
    limiter.set_crawl_delay("a.com", crawl_delay)
    assert limiter.delay_for("a.com") == expected


def test_default_above_cap_is_respected() -> None:
    limiter = HostRateLimiter(60.0)
    limiter.set_crawl_delay("a.com", 120.0)
    assert limiter.delay_for("a.com") == 60.0


async def test_crawl_delay_applies_to_its_host_only() -> None:
    fake = FakeTime()
    limiter = make_limiter(fake)
    limiter.set_crawl_delay("slow.com", 4.0)

    for host in ("slow.com", "slow.com", "fast.com", "fast.com"):
        await limiter.wait(host)

    assert fake.sleeps == [4.0, 1.0]


async def test_real_clock_spacing() -> None:
    limiter = HostRateLimiter(0.05)
    start = time.monotonic()

    await asyncio.gather(*(limiter.wait("a.com") for _ in range(3)))

    assert time.monotonic() - start >= 0.1
