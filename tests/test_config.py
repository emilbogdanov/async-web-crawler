import pytest
from pydantic import ValidationError

from crawler.config import Settings


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_defaults() -> None:
    settings = make_settings()
    assert settings.max_depth == 2
    assert settings.concurrency == 10
    assert settings.respect_robots is True


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRAWLER_MAX_DEPTH", "5")
    monkeypatch.setenv("CRAWLER_RESPECT_ROBOTS", "false")
    settings = make_settings()
    assert settings.max_depth == 5
    assert settings.respect_robots is False


def test_init_kwargs_win_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRAWLER_CONCURRENCY", "50")
    assert make_settings(concurrency=3).concurrency == 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_depth", -1),
        ("max_pages", 0),
        ("concurrency", 0),
        ("concurrency", 101),
        ("per_domain_delay", -0.1),
        ("request_timeout", 0),
        ("backoff_base", 0),
        ("user_agent", ""),
    ],
)
def test_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_is_immutable() -> None:
    settings = make_settings()
    with pytest.raises(ValidationError):
        settings.max_depth = 10  # type: ignore[misc]
