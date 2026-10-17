"""Command-line entry point. CLI flags override CRAWLER_* env vars and .env."""

import argparse
import asyncio
import logging
import time
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from pydantic import ValidationError

from crawler.config import Settings
from crawler.crawler import Crawler, CrawlStats
from crawler.exporters import open_exporter
from crawler.fetcher import build_client
from crawler.urls import normalize_url

logger = logging.getLogger("crawler")

DEFAULT_OUTPUT = Path("output/crawl.jsonl")
EXIT_OK, EXIT_FAILURE, EXIT_INTERRUPTED = 0, 1, 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crawler",
        description="Polite async web crawler. Exports to JSON Lines or SQLite.",
    )
    parser.add_argument("seeds", nargs="+", metavar="URL", help="start URL(s)")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="PATH",
        default=DEFAULT_OUTPUT,
        help="output file: .jsonl/.json or .db/.sqlite (default: %(default)s)",
    )
    parser.add_argument("-d", "--max-depth", type=int, metavar="N", help="link depth from seeds")
    parser.add_argument(
        "-n", "--max-pages", type=int, metavar="N", help="maximum pages to schedule"
    )
    parser.add_argument("-c", "--concurrency", type=int, metavar="N", help="concurrent requests")
    parser.add_argument(
        "--delay",
        dest="per_domain_delay",
        type=float,
        metavar="SECONDS",
        help="minimum delay between requests to one host",
    )
    parser.add_argument(
        "--timeout", dest="request_timeout", type=float, metavar="SECONDS", help="request timeout"
    )
    parser.add_argument(
        "--retries", dest="max_retries", type=int, metavar="N", help="retries per request"
    )
    parser.add_argument("--user-agent", metavar="UA", help="User-Agent header")
    parser.add_argument(
        "--ignore-robots",
        dest="respect_robots",
        action="store_false",
        default=None,
        help="do not fetch or obey robots.txt",
    )
    parser.add_argument(
        "--allow-external",
        dest="same_host_only",
        action="store_false",
        default=None,
        help="follow links to other hosts",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    verbosity.add_argument("-q", "--quiet", action="store_true", help="warnings and errors only")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('async-web-crawler')}"
    )
    return parser


def build_settings(args: argparse.Namespace) -> Settings:
    """Settings from env/.env, overridden by any flag the user actually passed."""
    overrides = {
        name: value
        for name, value in vars(args).items()
        if name in Settings.model_fields and value is not None
    }
    return Settings(**overrides)


def configure_logging(*, verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def run(settings: Settings, seeds: Sequence[str], output: Path) -> CrawlStats:
    async with build_client(settings) as client, open_exporter(output) as sink:
        return await Crawler(settings, client).crawl(seeds, sink)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose, quiet=args.quiet)

    try:
        settings = build_settings(args)
    except ValidationError as exc:
        errors = "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in exc.errors())
        parser.error(errors)
    invalid = [seed for seed in args.seeds if normalize_url(seed) is None]
    if invalid:
        parser.error(f"invalid seed URL(s): {', '.join(invalid)}")
    try:
        open_exporter(args.output)
    except ValueError as exc:
        parser.error(str(exc))

    started = time.perf_counter()
    try:
        stats = asyncio.run(run(settings, args.seeds, args.output))
    except KeyboardInterrupt:
        logger.warning("Interrupted; partial results saved to %s", args.output)
        return EXIT_INTERRUPTED
    except OSError as exc:
        logger.error("Cannot write %s: %s", args.output, exc)
        return EXIT_FAILURE
    except ExceptionGroup:
        logger.exception("Crawl aborted")
        return EXIT_FAILURE

    logger.info(
        "Done in %.1fs: %d pages, %d errors, %d blocked -> %s",
        time.perf_counter() - started,
        stats.pages,
        stats.errors,
        stats.blocked,
        args.output,
    )
    return EXIT_OK
