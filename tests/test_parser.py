import pytest

from crawler.parser import parse_html

PAGE = "https://example.com/blog/post"


def links_of(body: str, head: str = "", page_url: str = PAGE) -> tuple[str, ...]:
    html = f"<html><head>{head}</head><body>{body}</body></html>"
    return parse_html(html, page_url).links


def test_resolves_and_normalizes_links() -> None:
    body = """
        <a href="/about">About</a>
        <a href="next">Next</a>
        <a href="HTTPS://Other.com:443/x#frag">Other</a>
    """
    assert links_of(body) == (
        "https://example.com/about",
        "https://example.com/blog/next",
        "https://other.com/x",
    )


def test_deduplicates_preserving_first_occurrence_order() -> None:
    body = '<a href="/b">1</a><a href="/a">2</a><a href="/b#top">3</a><a href="/a?">4</a>'
    assert links_of(body) == ("https://example.com/b", "https://example.com/a")


@pytest.mark.parametrize(
    "href",
    ["mailto:me@example.com", "javascript:void(0)", "tel:+359888", "ftp://example.com/f"],
)
def test_skips_uncrawlable_schemes(href: str) -> None:
    assert links_of(f'<a href="{href}">x</a>') == ()


def test_skips_anchor_without_href_value() -> None:
    assert links_of("<a href>x</a><a>y</a>") == ()


def test_includes_area_links() -> None:
    assert links_of('<map><area href="/region"></map>') == ("https://example.com/region",)


@pytest.mark.parametrize("rel", ["nofollow", "NoFollow", "noopener nofollow", "ugc nofollow"])
def test_skips_rel_nofollow(rel: str) -> None:
    body = f'<a href="/skip" rel="{rel}">x</a><a href="/keep" rel="noopener">y</a>'
    assert links_of(body) == ("https://example.com/keep",)


@pytest.mark.parametrize(
    "meta",
    [
        '<meta name="robots" content="nofollow">',
        '<meta name="robots" content="noindex, nofollow">',
        '<meta name="robots" content="none">',
        '<META NAME="ROBOTS" CONTENT="NOFOLLOW">',
        '<meta name=" robots " content=" NoFollow ">',
    ],
)
def test_meta_robots_nofollow_suppresses_links(meta: str) -> None:
    assert links_of('<a href="/a">a</a>', head=meta) == ()


@pytest.mark.parametrize(
    "meta",
    [
        '<meta name="robots" content="noindex">',
        '<meta name="googlebot" content="nofollow">',
        '<meta name="robots">',
    ],
)
def test_other_meta_directives_keep_links(meta: str) -> None:
    assert links_of('<a href="/a">a</a>', head=meta) == ("https://example.com/a",)


def test_meta_nofollow_still_returns_title() -> None:
    html = '<head><title>T</title><meta name="robots" content="nofollow"></head><a href="/a">a</a>'
    page = parse_html(html, PAGE)
    assert page.title == "T"
    assert page.links == ()


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ('<base href="https://cdn.example.com/assets/">', "https://cdn.example.com/assets/img"),
        ('<base href="/root/">', "https://example.com/root/img"),
        ('<base href="javascript:void(0)">', "https://example.com/blog/img"),
        ("<base href>", "https://example.com/blog/img"),
        ('<base target="_blank">', "https://example.com/blog/img"),
    ],
)
def test_base_href(base: str, expected: str) -> None:
    assert links_of('<a href="img">x</a>', head=base) == (expected,)


def test_resolves_against_given_page_url() -> None:
    links = links_of('<a href="x">x</a>', page_url="https://example.com/redirected/")
    assert links == ("https://example.com/redirected/x",)


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        ("<title>  Hello\n World  </title>", "Hello\n World"),
        ("<title></title>", None),
        ("<title>   </title>", None),
        ("", None),
    ],
)
def test_title(head: str, expected: str | None) -> None:
    assert parse_html(f"<html><head>{head}</head></html>", PAGE).title == expected


@pytest.mark.parametrize(
    "html",
    ["", "not html at all", "<a href='/a'", "<html><body><div><a href=/b>unclosed", "\x00\xff<a>"],
)
def test_tolerates_malformed_html(html: str) -> None:
    parse_html(html, PAGE)


def test_unquoted_and_unclosed_links_are_found() -> None:
    assert links_of("<p><a href=/b>unclosed<a href=/c>") == (
        "https://example.com/b",
        "https://example.com/c",
    )
