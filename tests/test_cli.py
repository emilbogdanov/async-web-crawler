import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import respx

from crawler.cli import EXIT_FAILURE, EXIT_OK, build_parser, build_settings, main

SITE = "https://site.com"
FAST = ["--delay", "0", "--retries", "0", "--ignore-robots", "-q"]


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from an empty dir so a developer's .env or CRAWLER_* vars never leak in."""
    monkeypatch.chdir(tmp_path)
    for name in ("CRAWLER_MAX_DEPTH", "CRAWLER_CONCURRENCY", "CRAWLER_RESPECT_ROBOTS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SITE}/").respond(
            200,
            headers={"content-type": "text/html"},
            text='<title>Home</title><a href="/a">a</a>',
        )
        router.get(f"{SITE}/a").respond(
            200, headers={"content-type": "text/html"}, text="<title>A</title>"
        )
        yield router


def test_flags_override_only_what_was_passed() -> None:
    args = build_parser().parse_args([SITE, "-d", "4", "--ignore-robots", "--delay", "0.5"])
    settings = build_settings(args)

    assert settings.max_depth == 4
    assert settings.respect_robots is False
    assert settings.per_domain_delay == 0.5
    assert settings.same_host_only is True
    assert settings.concurrency == 10


def test_flags_win_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRAWLER_MAX_DEPTH", "7")
    monkeypatch.setenv("CRAWLER_CONCURRENCY", "3")

    settings = build_settings(build_parser().parse_args([SITE, "-d", "1"]))

    assert settings.max_depth == 1
    assert settings.concurrency == 3


def test_reads_dotenv_from_working_dir(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("CRAWLER_MAX_PAGES=7\n")
    assert build_settings(build_parser().parse_args([SITE])).max_pages == 7


def test_crawls_and_writes_jsonl(mock: respx.MockRouter, tmp_path: Path) -> None:
    output = tmp_path / "out" / "pages.jsonl"

    code = main([SITE, "-o", str(output), *FAST])

    assert code == EXIT_OK
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert {row["url"]: row["title"] for row in rows} == {f"{SITE}/": "Home", f"{SITE}/a": "A"}


def test_crawls_and_writes_sqlite(mock: respx.MockRouter, tmp_path: Path) -> None:
    output = tmp_path / "pages.db"
    assert main([SITE, "-o", str(output), *FAST]) == EXIT_OK
    assert output.exists()


def test_depth_flag_limits_crawl(mock: respx.MockRouter, tmp_path: Path) -> None:
    output = tmp_path / "pages.jsonl"

    main([SITE, "-o", str(output), "-d", "0", *FAST])

    assert len(output.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["not-a-url"], "invalid seed URL"),
        ([SITE, "-o", "out.csv"], "Unsupported output format"),
        ([SITE, "-c", "0"], "concurrency"),
        ([SITE, "-d", "-1"], "max_depth"),
        ([SITE, "-d", "abc"], "invalid int value"),
        ([], "required"),
        ([SITE, "-v", "-q"], "not allowed with"),
    ],
)
def test_usage_errors_exit_2(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(argv)

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


def test_unwritable_output_returns_failure(mock: respx.MockRouter, tmp_path: Path) -> None:
    output = tmp_path / "taken.jsonl"
    output.mkdir()

    assert main([SITE, "-o", str(output), *FAST]) == EXIT_FAILURE


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.startswith("crawler ")
