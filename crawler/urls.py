"""URL normalization and crawl scope."""

import hashlib
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlsplit, urlunsplit

HOST = "mangabaka.org"
ORIGIN = f"https://{HOST}"
# Attribute and tag patterns used for link extraction.
ATTRIBUTE_RE = re.compile(r'''(?<![\w:.-])([a-zA-Z_:][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))''')
TAG_RE = re.compile(r'''<[a-zA-Z][\w:-]*(?:[^"'<>]|"[^"]*"|'[^']*')*>''', re.S)
NON_LINK_RE = re.compile(r"<!--.*?-->|<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
DETAIL_RE = re.compile(r"^/manga/([0-9]+)(?:/([^/]+))?/?$")
STATIC_RE = re.compile(r"\.(?:avif|gif|jpe?g|png|svg|webp|ico|css|js|map|woff2?|ttf|pdf|zip|gz|mp[34])$", re.I)


@dataclass(frozen=True)
class CrawlURL:
    url: str
    key: str
    manga_id: str = ""

    @property
    def filename(self):
        if self.manga_id:
            return f"manga_{self.manga_id}.html"
        return f"list_{hashlib.sha256(self.url.encode('utf-8')).hexdigest()}.html"


def extract_hrefs(document: str):
    """Extract quoted href attributes outside scripts and comments."""
    for tag in TAG_RE.finditer(NON_LINK_RE.sub("", document)):
        for match in ATTRIBUTE_RE.finditer(tag.group()):
            if match.group(1).lower() == "href" and (match.group(2) is not None or match.group(3) is not None):
                yield unescape(match.group(2) if match.group(2) is not None else match.group(3))


def safe_absolute(raw: str, base: str = ORIGIN) -> str | None:
    """Origin gate used even for robots redirects; reject ambiguous paths."""
    raw = raw.strip()
    if not raw or "\\" in raw or any(ord(c) < 32 for c in raw):
        return None
    try:
        parts = urlsplit(urljoin(base, raw))
        if (parts.scheme != "https" or parts.hostname != HOST or parts.username
                or parts.password or parts.port not in (None, 443)):
            return None
        path = parts.path or "/"
        # Encoded delimiters/dot segments could bypass path and robots checks.
        decoded = unquote(path)
        if ("\\" in decoded or re.search(r"%(?:2f|5c|2e|25)", path, re.I)
                or any(p in (".", "..") for p in decoded.split("/"))):
            return None
        path = quote(path, safe="/%:@!$&'()*+,;=-._~")
        return urlunsplit(("https", HOST, path, parts.query, ""))
    except ValueError:
        return None


def normalize_url(raw: str, base: str = ORIGIN) -> CrawlURL | None:
    absolute = safe_absolute(raw, base)
    if absolute is None:
        return None
    parts = urlsplit(absolute)
    if STATIC_RE.search(unquote(parts.path)):
        return None
    match = DETAIL_RE.fullmatch(parts.path)
    if match:
        # Detail query variants (including tabs) are outside the crawl scope.
        if parts.query:
            return None
        manga_id = str(int(match.group(1)))
        slug = match.group(2)
        if slug and slug.lower() in {"covers", "related", "news", "collections", "works", "chapters", "read"}:
            return None
        path = f"/manga/{manga_id}" + (f"/{slug}" if slug else "")
        return CrawlURL(ORIGIN + path, "manga:" + manga_id, manga_id)
    if parts.path not in {"/search", "/search/", "/popular", "/popular/"}:
        return None
    path = parts.path.rstrip("/")
    query = ""
    if parts.query:
        # Pagination is supported only for /popular.
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        if (path != "/popular" or len(pairs) != 1 or pairs[0][0] != "page"
                or not re.fullmatch(r"[0-9]{1,4}", pairs[0][1])):
            return None
        page = int(pairs[0][1])
        if not 1 <= page <= 500:
            return None
        if page > 1:
            query = f"page={page}"
    url = urlunsplit(("https", HOST, path, query, ""))
    return CrawlURL(url, "list:" + url)


def discover_urls(document: str, base: str):
    seen = set()
    for href in extract_hrefs(document):
        target = normalize_url(href, base)
        if target and target.key not in seen:
            seen.add(target.key)
            yield target
