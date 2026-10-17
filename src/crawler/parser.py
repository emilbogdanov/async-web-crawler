"""HTML parsing: page title and crawlable outgoing links."""

from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser

from crawler.urls import normalize_url

_NOFOLLOW_DIRECTIVES = frozenset({"nofollow", "none"})


@dataclass(frozen=True, slots=True)
class ParsedPage:
    title: str | None
    links: tuple[str, ...]


def parse_html(html: str, page_url: str) -> ParsedPage:
    """Extract the title and normalized, deduplicated links from ``html``.

    ``page_url`` must be the final URL after redirects, since relative links resolve
    against it (or against ``<base href>`` when present). Links marked ``rel="nofollow"``
    are skipped; ``<meta name="robots" content="nofollow">`` suppresses all links.
    """
    tree = LexborHTMLParser(html)
    title_node = tree.css_first("title")
    title = (title_node.text(strip=True) or None) if title_node else None

    if _meta_robots_nofollow(tree):
        return ParsedPage(title=title, links=())

    base = _base_url(tree, page_url)
    links: dict[str, None] = {}
    for node in tree.css("a[href], area[href]"):
        href = node.attributes.get("href")
        if not href or _is_nofollow(node.attributes.get("rel")):
            continue
        url = normalize_url(href, base)
        if url is not None:
            links[url] = None

    return ParsedPage(title=title, links=tuple(links))


def _meta_robots_nofollow(tree: LexborHTMLParser) -> bool:
    for meta in tree.css("meta[name]"):
        name = meta.attributes.get("name") or ""
        if name.strip().lower() != "robots":
            continue
        content = meta.attributes.get("content") or ""
        directives = {token.strip().lower() for token in content.split(",")}
        if directives & _NOFOLLOW_DIRECTIVES:
            return True
    return False


def _base_url(tree: LexborHTMLParser, page_url: str) -> str:
    base_node = tree.css_first("base[href]")
    href = base_node.attributes.get("href") if base_node else None
    if not href:
        return page_url
    return normalize_url(href, page_url) or page_url


def _is_nofollow(rel: str | None) -> bool:
    return rel is not None and "nofollow" in rel.lower().split()
