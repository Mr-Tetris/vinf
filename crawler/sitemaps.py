"""Bounded standard-library XML sitemap parsing; no network access here."""

import gzip
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlsplit

from .urls import CrawlURL, normalize_url, safe_absolute

MAX_XML_BYTES = 50 * 1024 * 1024


def sitemap_url(raw, base='https://mangabaka.org'):
    url = safe_absolute(raw, base)
    if not url:
        return None
    parts = urlsplit(url)
    if parts.query or not (parts.path.endswith(('.xml', '.xml.gz')) or parts.path.startswith('/sitemap')):
        return None
    return CrawlURL(url, 'sitemap:' + url)


def declared_sitemaps(text):
    for line in text.splitlines():
        match = re.match(r'\s*Sitemap\s*:\s*(\S+)', line, re.I)
        if match and (target := sitemap_url(match[1])):
            yield target


@dataclass
class Sitemap:
    kind: str
    entries: int
    links: tuple
    truncated: bool


def parse_sitemap(raw, base, max_urls=50000):
    if raw.startswith(b'\x1f\x8b'):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                xml = stream.read(MAX_XML_BYTES + 1)
        except (OSError, EOFError) as error:
            raise ValueError('invalid_sitemap_gzip: ' + str(error)) from error
    else:
        xml = raw
    if len(xml) > MAX_XML_BYTES or re.search(br'<!DOCTYPE|<!ENTITY', xml, re.I):
        raise ValueError('unsafe_or_oversized_sitemap')
    links, seen, count, kind, truncated = [], set(), 0, None, False
    try:
        for event, element in ET.iterparse(io.BytesIO(xml), events=('start', 'end')):
            tag = element.tag.rsplit('}', 1)[-1]
            if kind is None:
                kind = tag
                if kind not in {'sitemapindex', 'urlset'}:
                    raise ValueError('not_a_sitemap')
            if event != 'end' or tag != ('sitemap' if kind == 'sitemapindex' else 'url'):
                continue
            count += 1
            location = next((child.text for child in element if child.tag.rsplit('}', 1)[-1] == 'loc'), '')
            if location:
                target = sitemap_url(location.strip(), base) if kind == 'sitemapindex' else normalize_url(location.strip(), base)
                if target and (kind == 'sitemapindex' or target.manga_id) and target.key not in seen:
                    if len(links) < max_urls:
                        seen.add(target.key)
                        links.append(target)
                    else:
                        truncated = True
            element.clear()
    except (ET.ParseError, OSError, EOFError) as error:
        raise ValueError('invalid_sitemap_xml: ' + str(error)) from error
    if kind is None:
        raise ValueError('empty_sitemap')
    return Sitemap(kind, count, tuple(links), truncated)
