import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crawler.exporters import JsonLinesExporter, SqliteExporter, open_exporter
from crawler.models import PageRecord

FETCHED_AT = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def make_record(url: str = "https://a.com/", **overrides: object) -> PageRecord:
    fields: dict[str, object] = {
        "url": url,
        "final_url": url,
        "depth": 0,
        "fetched_at": FETCHED_AT,
        "status_code": 200,
        "content_type": "text/html",
        "title": "Home",
        "links": ("https://a.com/x", "https://a.com/y"),
    }
    return PageRecord(**(fields | overrides))  # type: ignore[arg-type]


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def query(path: Path, sql: str) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchall()


async def test_jsonl_writes_one_object_per_line(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    async with JsonLinesExporter(path) as sink:
        await sink(make_record())
        await sink(make_record("https://a.com/x", depth=1, links=(), title=None))

    rows = read_jsonl(path)
    assert rows[0] == {
        "url": "https://a.com/",
        "final_url": "https://a.com/",
        "depth": 0,
        "fetched_at": "2026-10-18T12:00:00+00:00",
        "status_code": 200,
        "content_type": "text/html",
        "title": "Home",
        "links": ["https://a.com/x", "https://a.com/y"],
        "error": None,
    }
    assert rows[1]["links"] == []
    assert rows[1]["title"] is None


async def test_jsonl_keeps_unicode_readable(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    async with JsonLinesExporter(path) as sink:
        await sink(make_record(title="Здравей"))

    assert "Здравей" in path.read_text(encoding="utf-8")


async def test_jsonl_replaces_existing_file_and_creates_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "out.jsonl"
    for _ in range(2):
        async with JsonLinesExporter(path) as sink:
            await sink(make_record())

    assert len(read_jsonl(path)) == 1


async def test_sqlite_stores_pages_and_links(tmp_path: Path) -> None:
    path = tmp_path / "out.db"
    async with SqliteExporter(path) as sink:
        await sink(make_record())
        await sink(
            make_record("https://a.com/x", depth=1, status_code=None, error="boom", links=())
        )

    assert query(path, "SELECT url, depth, status_code, title, error FROM pages ORDER BY url") == [
        ("https://a.com/", 0, 200, "Home", None),
        ("https://a.com/x", 1, None, "Home", "boom"),
    ]
    assert query(path, "SELECT source, target FROM links ORDER BY target") == [
        ("https://a.com/", "https://a.com/x"),
        ("https://a.com/", "https://a.com/y"),
    ]


async def test_sqlite_rerun_upserts_and_replaces_links(tmp_path: Path) -> None:
    path = tmp_path / "out.db"
    async with SqliteExporter(path) as sink:
        await sink(make_record())
    async with SqliteExporter(path) as sink:
        await sink(make_record(title="New", links=("https://a.com/z",)))

    assert query(path, "SELECT title FROM pages") == [("New",)]
    assert query(path, "SELECT target FROM links") == [("https://a.com/z",)]


async def test_sqlite_deduplicates_links(tmp_path: Path) -> None:
    path = tmp_path / "out.db"
    async with SqliteExporter(path) as sink:
        await sink(make_record(links=("https://a.com/x", "https://a.com/x")))

    assert query(path, "SELECT COUNT(*) FROM links") == [(1,)]


async def test_sqlite_commits_in_batches(tmp_path: Path) -> None:
    path = tmp_path / "out.db"
    async with SqliteExporter(path, batch_size=2) as sink:
        await sink(make_record("https://a.com/1"))
        assert query(path, "SELECT COUNT(*) FROM pages") == [(0,)]
        await sink(make_record("https://a.com/2"))
        assert query(path, "SELECT COUNT(*) FROM pages") == [(2,)]
        await sink(make_record("https://a.com/3"))

    assert query(path, "SELECT COUNT(*) FROM pages") == [(3,)]


async def test_sqlite_keeps_partial_results_on_error(tmp_path: Path) -> None:
    path = tmp_path / "out.db"

    async def crashing_crawl() -> None:
        async with SqliteExporter(path) as sink:
            await sink(make_record())
            raise RuntimeError("crawl died")

    with pytest.raises(RuntimeError, match="crawl died"):
        await crashing_crawl()

    assert query(path, "SELECT COUNT(*) FROM pages") == [(1,)]


@pytest.mark.parametrize("exporter_cls", [JsonLinesExporter, SqliteExporter])
async def test_writing_before_open_raises(
    tmp_path: Path, exporter_cls: type[JsonLinesExporter] | type[SqliteExporter]
) -> None:
    with pytest.raises(RuntimeError, match="not open"):
        await exporter_cls(tmp_path / "out")(make_record())


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("out.jsonl", JsonLinesExporter),
        ("out.json", JsonLinesExporter),
        ("OUT.JSONL", JsonLinesExporter),
        ("out.db", SqliteExporter),
        ("out.sqlite", SqliteExporter),
        ("out.sqlite3", SqliteExporter),
    ],
)
def test_open_exporter_picks_by_suffix(name: str, expected: type) -> None:
    assert isinstance(open_exporter(Path(name)), expected)


@pytest.mark.parametrize("name", ["out.csv", "out", "out.txt"])
def test_open_exporter_rejects_unknown_suffix(name: str) -> None:
    with pytest.raises(ValueError, match="Unsupported output format"):
        open_exporter(Path(name))
