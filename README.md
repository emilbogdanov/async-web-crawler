# Async Web Crawler

Polite, concurrent web crawler built on asyncio. It respects robots.txt, rate-limits per host, and streams results to JSON Lines or SQLite.

[![CI](https://github.com/ESBogdanov25/async-web-crawler/actions/workflows/ci.yml/badge.svg)](https://github.com/ESBogdanov25/async-web-crawler/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy: strict](https://img.shields.io/badge/mypy-strict-blue)](https://mypy-lang.org/)

## Features

- **Bounded concurrency**: a fixed pool of async workers shares one HTTP connection pool.
- **robots.txt (RFC 9309)**:
  - The longest matching rule wins, and `*`/`$` wildcards are supported.
  - Each origin's file is fetched once, even when many workers ask at the same time.
  - A missing file (4xx) allows everything; an unreachable one (5xx) blocks the origin.
  - `Crawl-delay` is honored.
- **Per-host rate limiting**: a minimum gap between requests to the same host, raised to the site's `Crawl-delay` when that is longer. Different hosts never block each other.
- **Depth and page limits**: crawls breadth-first; memory stays bounded by `max_pages`.
- **Deduplication**: URLs are normalized first, so `HTTP://Site.com:80/a/../b?y=2&x=1#top` and `http://site.com/b?x=1&y=2` count as the same page.
- **Retries**: on timeouts, network errors and 429/5xx responses, with jittered exponential backoff and `Retry-After` support.
- **Safety limits**:
  - Response bodies are capped by size, measured after decompression.
  - Only HTML bodies are downloaded; other content types cost just the headers.
- **Link hygiene**:
  - `rel="nofollow"`, `<meta name="robots" content="nofollow">` and `<base href>` are respected.
  - If a seed URL redirects to a new host (e.g. `www.`), the crawl follows it there.
- **Streaming export**:
  - **JSON Lines**: one record per line.
  - **SQLite**: `pages` and `links` tables, committed in batches. A run that crashes or is interrupted keeps what it already wrote.

## Tech stack

- **HTTP and parsing**: Python 3.12+, asyncio, [httpx](https://www.python-httpx.org/), [selectolax](https://github.com/rushter/selectolax) (Lexbor HTML parser), [protego](https://github.com/scrapy/protego) (robots.txt)
- **Configuration**: [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- **Tooling**: uv, ruff, mypy (strict), pytest, pytest-asyncio, respx, pre-commit, GitHub Actions, Docker

## Quickstart

```bash
git clone https://github.com/ESBogdanov25/async-web-crawler.git && cd async-web-crawler
uv sync
uv run crawler https://pypi.org/project/httpx/ -d 1 -n 10
```

Or with Docker:

```bash
docker build -t async-web-crawler .
docker run --rm -v "$PWD/output:/data/output" async-web-crawler https://pypi.org/project/httpx/ -d 1 -n 10
```

## Usage

```text
crawler [-h] [-o PATH] [-d N] [-n N] [-c N] [--delay SECONDS] [--timeout SECONDS]
        [--retries N] [--user-agent UA] [--ignore-robots] [--allow-external]
        [-v | -q] [--version] URL [URL ...]
```

### Crawl to JSON Lines

```console
$ uv run crawler https://pypi.org/project/httpx/ -d 1 -n 10
23:36:14 INFO    crawler.crawler: Fetched https://pypi.org/project/httpx/ [200] depth=0 links=143
23:36:14 INFO    crawler.crawler: Blocked by robots.txt: https://pypi.org/account/login/?next=https%3A%2F%2Fpypi.org%2Fproject%2Fhttpx%2F
23:36:14 INFO    crawler.crawler: Blocked by robots.txt: https://pypi.org/account/register/
23:36:14 INFO    crawler.crawler: Blocked by robots.txt: https://pypi.org/search/?c=Development+Status+%3A%3A+4+-+Beta
23:36:14 INFO    crawler.crawler: Blocked by robots.txt: https://pypi.org/search/?c=Environment+%3A%3A+Web+Environment
23:36:14 INFO    crawler.crawler: Blocked by robots.txt: https://pypi.org/search/?c=Framework+%3A%3A+AsyncIO
23:36:15 INFO    crawler.crawler: Fetched https://pypi.org/ [200] depth=1 links=44
23:36:16 INFO    crawler.crawler: Fetched https://pypi.org/help/ [200] depth=1 links=120
23:36:17 INFO    crawler.crawler: Fetched https://pypi.org/user/cagil/ [200] depth=1 links=0
23:36:18 INFO    crawler.crawler: Fetched https://pypi.org/user/lovelydinosaur/ [200] depth=1 links=0
23:36:18 INFO    crawler.crawler: Crawl finished: 5 pages, 0 errors, 5 blocked
23:36:18 INFO    crawler: Done in 4.4s: 5 pages, 0 errors, 5 blocked -> output/crawl.jsonl
```

Each line of `output/crawl.jsonl` is one page (the `links` array is trimmed here; the full record had 143):

```json
{
  "url": "https://pypi.org/project/httpx/",
  "final_url": "https://pypi.org/project/httpx/",
  "depth": 0,
  "fetched_at": "2026-10-17T23:36:40.154090+00:00",
  "status_code": 200,
  "content_type": "text/html; charset=UTF-8",
  "title": "httpx · PyPI",
  "links": ["https://pypi.org/project/httpx/", "https://pypi.org/"],
  "error": null
}
```

Pages that weren't fetched are still recorded, with the reason:

```json
{"url": "https://pypi.org/account/register/", "depth": 1, "status_code": null, "error": "blocked by robots.txt", "...": "..."}
```

### Crawl to SQLite and query the link graph

```console
$ uv run crawler https://pypi.org/project/httpx/ -d 1 -n 10 -o output/pypi.db -q
$ sqlite3 output/pypi.db "SELECT status_code, title, url FROM pages WHERE error IS NULL"
200|httpx · PyPI|https://pypi.org/project/httpx/
200|PyPI · The Python Package Index|https://pypi.org/
200|Help · PyPI|https://pypi.org/help/
200|Client Challenge|https://pypi.org/user/cagil/
200|Client Challenge|https://pypi.org/user/lovelydinosaur/
$ sqlite3 output/pypi.db "SELECT target, COUNT(*) AS inbound FROM links GROUP BY target ORDER BY inbound DESC LIMIT 3"
https://about.facebook.com/meta/|3
https://aka.ms/python|3
https://aws.amazon.com/|3
```

### More examples

```bash
# Several seeds, stay on their hosts, faster but still polite
uv run crawler https://example.com https://example.org -c 20 --delay 0.5

# Follow external links, deeper crawl, verbose logs
uv run crawler https://example.com --allow-external -d 3 -n 500 -v

# Run as a module
uv run python -m crawler https://example.com
```

## Project structure

```text
async-web-crawler/
├── src/crawler/
│   ├── cli.py         # argparse entry point; flags override env
│   ├── config.py      # Settings (pydantic-settings, CRAWLER_* env vars)
│   ├── crawler.py     # worker pool (TaskGroup) wiring everything together
│   ├── frontier.py    # BFS queue: dedup, depth, page and host limits
│   ├── fetcher.py     # httpx fetch with retries, backoff, size cap
│   ├── robots.py      # per-origin robots.txt cache (RFC 9309)
│   ├── ratelimit.py   # per-host delay scheduler
│   ├── parser.py      # title + link extraction (selectolax)
│   ├── urls.py        # URL normalization
│   ├── models.py      # PageRecord, PageSink
│   ├── exporters.py   # JSON Lines and SQLite sinks
│   └── __main__.py    # python -m crawler
├── tests/             # one test module per source module
├── Dockerfile         # multi-stage uv build, non-root runtime
├── .github/workflows/ci.yml
├── .pre-commit-config.yaml
├── .env.example
└── pyproject.toml
```

## Configuration

Settings are read from environment variables or a `.env` file in the working directory (see [.env.example](.env.example)). A CLI flag overrides the matching variable.

| Variable | CLI flag | Default | Description |
|---|---|---|---|
| `CRAWLER_MAX_DEPTH` | `-d`, `--max-depth` | `2` | Link depth from the seeds (`0` = seeds only) |
| `CRAWLER_MAX_PAGES` | `-n`, `--max-pages` | `100` | Maximum pages scheduled per crawl |
| `CRAWLER_CONCURRENCY` | `-c`, `--concurrency` | `10` | Concurrent workers/connections (1–100) |
| `CRAWLER_PER_DOMAIN_DELAY` | `--delay` | `1.0` | Minimum seconds between requests to one host |
| `CRAWLER_REQUEST_TIMEOUT` | `--timeout` | `10.0` | Per-request timeout in seconds |
| `CRAWLER_MAX_RETRIES` | `--retries` | `3` | Retries on network errors and 429/5xx |
| `CRAWLER_BACKOFF_BASE` | | `0.5` | First retry delay ceiling in seconds; doubles each attempt |
| `CRAWLER_MAX_RESPONSE_BYTES` | | `5242880` | Body size cap (decompressed) |
| `CRAWLER_USER_AGENT` | `--user-agent` | `AsyncWebCrawler/0.1 (+repo URL)` | Sent with every request and matched against robots.txt |
| `CRAWLER_RESPECT_ROBOTS` | `--ignore-robots` | `true` | Fetch and obey robots.txt |
| `CRAWLER_SAME_HOST_ONLY` | `--allow-external` | `true` | Only follow links on the seeds' hosts |

The output path is chosen with `-o`, `--output` (default `output/crawl.jsonl`). The suffix picks the format: `.jsonl`/`.json` for JSON Lines, `.db`/`.sqlite`/`.sqlite3` for SQLite.

## Running tests

```bash
uv run pytest            # 232 tests, ~4s, no network access
uv run ruff check . && uv run ruff format --check .
uv run mypy              # strict, src and tests
uv run pre-commit install  # run all of the above on every commit
```

All HTTP is mocked with [respx](https://lundberg.github.io/respx/). Time is injected as `sleep`/`clock` functions, so the retry, backoff and rate-limit tests check exact delays without real waiting.

## Design notes

- **Pure core, I/O at the edges.** `urls`, `parser`, `frontier` and `ratelimit` do no I/O, so they are tested directly. `fetcher`, `robots` and `exporters` own the network and disk, and `crawler.py` only wires them together.
- **Lock-free rate limiting.** Each call reserves the host's next free time slot without any `await` between reading and writing it. On a single-threaded event loop, that makes concurrent callers queue up in order without needing a lock.
- **Fail fast on bugs, record expected failures.** Network errors, oversized bodies and robots.txt blocks become records with an `error` field. Anything else, such as a failing disk write, cancels the whole `TaskGroup` instead of being silently swallowed in a worker.
- **Streaming output.** Records go to the sink as they are produced, so memory doesn't grow with crawl size. SQLite writes use the standard library `sqlite3` driver with WAL mode and batched commits. They contain no `await`, so concurrent workers can share one connection safely.
- **Chosen libraries over the standard library where it matters.**
  - `urllib.robotparser` applies the first matching rule instead of the longest, and has no wildcard support, so protego is used instead.
  - selectolax is several times faster than `html.parser`, which matters because parsing runs on the event loop.

## Limitations & roadmap

**Limitations**

- **Redirects skip robots.txt.** httpx follows redirects automatically, so a redirect to another origin is fetched without checking that origin's robots.txt.
- **A failed robots.txt blocks the origin for the whole run.** It is fetched once with no retry, so one 5xx or timeout blocks the origin until the crawl ends (strict, but safe).
- **No JavaScript rendering.** Pages that build their links client-side yield no links.
- **Query-parameter sorting can over-merge.** Sorting parameters for dedup wrongly merges URLs on the rare servers where parameter order matters.
- **No resume.** An interrupted crawl starts again from the seeds.

**Roadmap**

- Handle redirects manually, so every hop goes through robots.txt and the rate limiter
- Resume from SQLite: rebuild the frontier from stored pages and links
- `sitemap.xml` discovery
- Prometheus metrics (requests, latency, retries, status codes)
- Conditional requests (`ETag`/`Last-Modified`) for re-crawls

## License

[MIT](LICENSE) © 2026 Emil Bogdanov
