"""Streaming page sinks: JSON Lines and SQLite.

Each exporter is an async context manager and a ``PageSink``. Writes are synchronous
and contain no ``await``, so concurrent workers can share one exporter safely.
"""

import json
import sqlite3
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TextIO

from crawler.models import PageRecord

JSONL_SUFFIXES = frozenset({".jsonl", ".json"})
SQLITE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url          TEXT PRIMARY KEY,
    final_url    TEXT NOT NULL,
    depth        INTEGER NOT NULL,
    fetched_at   TEXT NOT NULL,
    status_code  INTEGER,
    content_type TEXT,
    title        TEXT,
    error        TEXT
);
CREATE TABLE IF NOT EXISTS links (
    source TEXT NOT NULL REFERENCES pages(url) ON DELETE CASCADE,
    target TEXT NOT NULL,
    PRIMARY KEY (source, target)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_links_target ON links(target);
"""

_UPSERT_PAGE = """
INSERT INTO pages (url, final_url, depth, fetched_at, status_code, content_type, title, error)
VALUES (:url, :final_url, :depth, :fetched_at, :status_code, :content_type, :title, :error)
ON CONFLICT(url) DO UPDATE SET
    final_url = excluded.final_url,
    depth = excluded.depth,
    fetched_at = excluded.fetched_at,
    status_code = excluded.status_code,
    content_type = excluded.content_type,
    title = excluded.title,
    error = excluded.error
"""


def record_to_dict(record: PageRecord) -> dict[str, Any]:
    data = asdict(record)
    data["fetched_at"] = record.fetched_at.isoformat()
    data["links"] = list(record.links)
    return data


class JsonLinesExporter:
    """Writes one JSON object per line; the file is replaced on each run."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: TextIO | None = None

    async def __aenter__(self) -> Self:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("w", encoding="utf-8", newline="\n")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    async def __call__(self, record: PageRecord) -> None:
        if self._file is None:
            raise RuntimeError("Exporter is not open")
        self._file.write(json.dumps(record_to_dict(record), ensure_ascii=False) + "\n")


class SqliteExporter:
    """Upserts pages and their outgoing links; commits every ``batch_size`` records."""

    def __init__(self, path: Path, *, batch_size: int = 100) -> None:
        self._path = path
        self._batch_size = batch_size
        self._pending = 0
        self._conn: sqlite3.Connection | None = None

    async def __aenter__(self) -> Self:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(_SCHEMA)
        self._conn = conn
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._conn is not None:
            self._conn.commit()  # keep partial results even if the crawl failed
            self._conn.close()
            self._conn = None

    async def __call__(self, record: PageRecord) -> None:
        if self._conn is None:
            raise RuntimeError("Exporter is not open")
        row = record_to_dict(record)
        del row["links"]
        self._conn.execute(_UPSERT_PAGE, row)
        self._conn.execute("DELETE FROM links WHERE source = ?", (record.url,))
        self._conn.executemany(
            "INSERT OR IGNORE INTO links (source, target) VALUES (?, ?)",
            ((record.url, target) for target in record.links),
        )
        self._pending += 1
        if self._pending >= self._batch_size:
            self._conn.commit()
            self._pending = 0


type Exporter = JsonLinesExporter | SqliteExporter


def open_exporter(path: Path) -> AbstractAsyncContextManager[Exporter]:
    """Pick an exporter from the output file's suffix."""
    suffix = path.suffix.lower()
    if suffix in JSONL_SUFFIXES:
        return JsonLinesExporter(path)
    if suffix in SQLITE_SUFFIXES:
        return SqliteExporter(path)
    supported = ", ".join(sorted(JSONL_SUFFIXES | SQLITE_SUFFIXES))
    raise ValueError(f"Unsupported output format {suffix!r}; use one of: {supported}")
