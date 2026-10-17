import asyncio

import pytest

from crawler.frontier import CrawlTask, Frontier

A = "https://a.com/"


def make_frontier(
    max_depth: int = 2, max_pages: int = 100, allowed_hosts: frozenset[str] | None = None
) -> Frontier:
    return Frontier(max_depth=max_depth, max_pages=max_pages, allowed_hosts=allowed_hosts)


async def drain(frontier: Frontier, count: int) -> list[CrawlTask]:
    tasks = [await frontier.get() for _ in range(count)]
    for _ in tasks:
        frontier.task_done()
    return tasks


async def test_is_breadth_first_fifo() -> None:
    frontier = make_frontier()
    frontier.add(A, 0)
    frontier.add(A + "a", 1)
    frontier.add(A + "b", 1)
    frontier.add(A + "a/x", 2)

    tasks = await drain(frontier, 4)

    assert [(t.url, t.depth) for t in tasks] == [
        (A, 0),
        (A + "a", 1),
        (A + "b", 1),
        (A + "a/x", 2),
    ]


def test_rejects_duplicates() -> None:
    frontier = make_frontier()
    assert frontier.add(A, 0) is True
    assert frontier.add(A, 1) is False
    assert frontier.scheduled == 1


@pytest.mark.parametrize(("depth", "accepted"), [(0, True), (2, True), (3, False)])
def test_depth_limit(depth: int, accepted: bool) -> None:
    assert make_frontier(max_depth=2).add(A, depth) is accepted


def test_zero_depth_allows_only_seeds() -> None:
    frontier = make_frontier(max_depth=0)
    assert frontier.add(A, 0) is True
    assert frontier.add(A + "child", 1) is False


def test_max_pages_caps_scheduling() -> None:
    frontier = make_frontier(max_pages=3)
    results = [frontier.add(f"{A}{i}", 1) for i in range(5)]
    assert results == [True, True, True, False, False]
    assert frontier.scheduled == 3


def test_rejected_by_depth_does_not_mark_seen() -> None:
    frontier = make_frontier(max_depth=1)
    assert frontier.add(A + "x", 2) is False
    assert frontier.add(A + "x", 1) is True


def test_mark_seen_blocks_alias_without_using_budget() -> None:
    frontier = make_frontier(max_pages=1)
    frontier.mark_seen(A + "redirected")

    assert frontier.add(A + "redirected", 1) is False
    assert frontier.add(A, 0) is True
    assert frontier.scheduled == 1


@pytest.mark.parametrize(
    ("url", "accepted"),
    [
        ("https://a.com/x", True),
        ("http://a.com/x", True),
        ("https://sub.a.com/x", False),
        ("https://a.com:8080/x", False),
        ("https://b.com/x", False),
    ],
)
def test_allowed_hosts(url: str, accepted: bool) -> None:
    frontier = make_frontier(allowed_hosts=frozenset({"a.com"}))
    assert frontier.add(url, 0) is accepted


def test_no_host_restriction_by_default() -> None:
    frontier = make_frontier()
    assert frontier.add("https://anything.org/", 0) is True


async def test_join_waits_for_all_tasks_done() -> None:
    frontier = make_frontier()
    frontier.add(A, 0)
    frontier.add(A + "b", 1)

    await frontier.get()
    frontier.task_done()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(frontier.join(), timeout=0.05)

    await frontier.get()
    frontier.task_done()
    await asyncio.wait_for(frontier.join(), timeout=0.05)


async def test_join_returns_immediately_when_empty() -> None:
    await asyncio.wait_for(make_frontier().join(), timeout=0.05)


async def test_tasks_added_during_processing_are_awaited() -> None:
    frontier = make_frontier()
    frontier.add(A, 0)

    task = await frontier.get()
    frontier.add(task.url + "child", task.depth + 1)
    frontier.task_done()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(frontier.join(), timeout=0.05)
    await drain(frontier, 1)
    await asyncio.wait_for(frontier.join(), timeout=0.05)


def test_allow_host_extends_allow_list() -> None:
    frontier = make_frontier(allowed_hosts=frozenset({"a.com"}))
    assert frontier.add("https://www.a.com/", 1) is False

    frontier.allow_host("www.a.com")

    assert frontier.add("https://www.a.com/", 1) is True


def test_allow_host_is_noop_without_restriction() -> None:
    frontier = make_frontier()
    frontier.allow_host("a.com")
    assert frontier.add("https://other.com/", 0) is True
