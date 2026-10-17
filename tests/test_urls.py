import pytest

from crawler.urls import host_of, normalize_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://example.com", "http://example.com/"),
        ("HTTP://Example.COM/Path", "http://example.com/Path"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        ("http://example.com/a#section", "http://example.com/a"),
        ("http://example.com/a/./b/../c", "http://example.com/a/c"),
        ("http://example.com/../../a", "http://example.com/a"),
        ("http://example.com/a/b/..", "http://example.com/a/"),
        ("http://example.com/a//b", "http://example.com/a//b"),
        ("http://example.com/a/", "http://example.com/a/"),
        ("http://example.com/?b=2&a=1", "http://example.com/?a=1&b=2"),
        ("http://example.com/?a=&b=1", "http://example.com/?a=&b=1"),
        ("http://user:secret@example.com/", "http://example.com/"),
        ("http://[::1]:8080/", "http://[::1]:8080/"),
        ("  http://example.com/a  ", "http://example.com/a"),
    ],
)
def test_normalize_absolute(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("/about", "https://example.com/about"),
        ("contact", "https://example.com/blog/contact"),
        ("../root", "https://example.com/root"),
        ("?page=2", "https://example.com/blog/post?page=2"),
        ("#top", "https://example.com/blog/post"),
        ("//cdn.example.com/x", "https://cdn.example.com/x"),
        ("http://other.com/x", "http://other.com/x"),
    ],
)
def test_normalize_relative(url: str, expected: str) -> None:
    assert normalize_url(url, base="https://example.com/blog/post") == expected


@pytest.mark.parametrize(
    "url",
    [
        "mailto:someone@example.com",
        "javascript:void(0)",
        "ftp://example.com/file",
        "tel:+359888000000",
        "http://",
        "http://example.com:99999/",
        "http://example.com:abc/",
        "/relative/without/base",
        "",
    ],
)
def test_normalize_rejects_uncrawlable(url: str) -> None:
    assert normalize_url(url) is None


def test_normalize_is_idempotent() -> None:
    once = normalize_url("HTTP://Example.com:80/a/../b?z=1&a=2#frag")
    assert once is not None
    assert normalize_url(once) == once


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a", "example.com"),
        ("https://example.com:8443/a", "example.com:8443"),
    ],
)
def test_host_of(url: str, expected: str) -> None:
    assert host_of(url) == expected
