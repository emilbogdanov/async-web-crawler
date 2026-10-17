"""URL normalization used for deduplication and per-host bookkeeping."""

from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Return a canonical absolute http(s) URL, or ``None`` if ``url`` is not crawlable.

    Resolves ``url`` against ``base``, lowercases scheme and host, drops credentials,
    default ports and fragments, removes dot segments and sorts query parameters.
    """
    absolute = urljoin(base, url.strip()) if base else url.strip()
    parts = urlsplit(absolute)

    if parts.scheme not in _ALLOWED_SCHEMES or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None

    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    netloc = host if port in (None, _DEFAULT_PORTS[parts.scheme]) else f"{host}:{port}"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))

    return urlunsplit((parts.scheme, netloc, _remove_dot_segments(parts.path), query, ""))


def host_of(url: str) -> str:
    """Return the lowercase host (with non-default port) of a normalized URL."""
    return urlsplit(url).netloc


def _remove_dot_segments(path: str) -> str:
    """Resolve ``.`` and ``..`` segments (RFC 3986 §5.2.4); empty path becomes ``/``."""
    segments = path.split("/")
    output: list[str] = []
    for segment in segments:
        if segment == "..":
            if len(output) > 1:
                output.pop()
        elif segment != ".":
            output.append(segment)
    if segments[-1] in {".", ".."}:
        output.append("")
    return "/".join(output) or "/"
