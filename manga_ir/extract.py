"""Field extraction from observed MangaBaka SSR HTML; JSON-LD is auxiliary only."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

from crawler.storage import read_tsv, write_tsv
from crawler.urls import ATTRIBUTE_RE, normalize_url
from crawler.validation import blocked_document

MANGA_FIELDS = ['manga_id', 'title', 'description', 'work_type', 'status', 'start_year',
                'end_year', 'chapters', 'volumes', 'source_url', 'html_path', 'html_sha256']
TITLE_FIELDS = ['manga_id', 'title', 'language', 'origin']
CREATOR_FIELDS = ['manga_id', 'name', 'role', 'origin']
TAG_FIELDS = ['manga_id', 'name', 'kind', 'tag_id', 'origin']
ERROR_FIELDS = ['html_path', 'source_url', 'error']
SCRIPT_RE = re.compile(r'<script\b[^>]*>.*?</script\s*>|<!--.*?-->', re.I | re.S)
H1_RE = re.compile(r'<h1\b[^>]*>(.*?)</h1\s*>', re.I | re.S)
DESCRIPTION_RE = re.compile(r'<section\b[^>]*\baria-label=[\'"]Description[\'"][^>]*>(.*?)</section\s*>', re.I | re.S)
ANCHOR_RE = re.compile(r'<a\b([^>]*)>(.*?)</a\s*>', re.I | re.S)
LANG_TITLE_RE = re.compile(r'<div\b[^>]*\blang=[\'"]([^\'"]+)[\'"][^>]*>(.*?)</div\s*>', re.I | re.S)
OPEN_TAG_RE = re.compile(r'<[A-Za-z][\w:-]*(?:[^"\'<>]|"[^"]*"|\'[^\']*\')*>')


def clean_text(value):
    value = re.sub(r'<(?:br\b[^>]*|/p\s*|/div\s*|hr\b[^>]*)>', ' ', value, flags=re.I)
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]*>', ' ', value))).strip()


def attrs(tag):
    return {m[1].lower(): unescape(next(v for v in m.groups()[1:] if v is not None))
            for m in ATTRIBUTE_RE.finditer(tag)}


def unique(values):
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            yield value


@dataclass
class Manga:
    scalar: dict
    titles: list = field(default_factory=list)
    creators: list = field(default_factory=list)
    tags: list = field(default_factory=list)


def matching_jsonld(raw, manga_id):
    """Auxiliary role/alternate-title source, selected by exact identifier, not name."""
    for match in re.finditer(r'<script\b([^>]*)>(.*?)</script\s*>', raw, re.I | re.S):
        if attrs(match[1]).get('type') != 'application/ld+json':
            continue
        try:
            item = json.loads(match[2])
        except (ValueError, TypeError):
            continue
        if not isinstance(item, dict) or item.get('@type') != 'ComicSeries':
            continue
        identity = item.get('identifier', {})
        if isinstance(identity, dict) and identity.get('propertyID') == 'mangabaka' and str(identity.get('value')) == manga_id:
            return item
    return {}


def extract_html(raw: bytes, source_url: str, html_path: str) -> Manga:
    target = normalize_url(source_url)
    if target is None or not target.manga_id:
        raise ValueError('not_a_manga_detail')
    text = raw.decode('utf-8-sig', errors='strict')
    if reason := blocked_document(raw):
        raise ValueError(reason)
    dom = SCRIPT_RE.sub('', text)
    page_title = re.search(r'<title\b[^>]*>(.*?)</title\s*>', dom, re.I | re.S)
    if not page_title or 'mangabaka' not in clean_text(page_title[1]).casefold():
        raise ValueError('unrecognized_document')
    canonical = next((attrs(tag).get('href', '') for tag in OPEN_TAG_RE.findall(dom)
                      if tag.lower().startswith('<link') and attrs(tag).get('rel') == 'canonical'), '')
    canonical_target = normalize_url(canonical)
    if canonical_target is None or canonical_target.manga_id != target.manga_id:
        raise ValueError('missing_or_mismatched_canonical')
    heading = H1_RE.search(dom)
    if not heading or not clean_text(heading[1]):
        raise ValueError('missing_title')
    title = clean_text(heading[1])
    if re.match(r'(?:404|403|error|not found|sign in|access denied)\b', title, re.I):
        raise ValueError('error_document')
    # Primary header/meta precede Description; recommendations follow the main Tags.
    description = DESCRIPTION_RE.search(dom, heading.end())
    meta_end = description.start() if description else dom.find('<h2', heading.end())
    if meta_end < 0:
        meta_end = len(dom)
    meta = dom[heading.start():meta_end]
    labels = [attrs(tag).get('aria-label', '') for tag in OPEN_TAG_RE.findall(meta)]
    def label(pattern):
        return next((m[1] for v in labels if (m := re.fullmatch(pattern, v))), '')
    years = next((m for v in labels if (m := re.fullmatch(r'Published ([0-9]{4})(?:\s*-\s*([0-9]{4}))?', v))), None)
    scalar = dict.fromkeys(MANGA_FIELDS, '')
    scalar.update(manga_id=target.manga_id, title=title,
                  description=clean_text(description[1]) if description else '',
                  work_type=label(r'Published as (.+)'), status=label(r'Series is (releasing|completed|hiatus|cancelled|upcoming|unknown)'),
                  start_year=years[1] if years else '', end_year=(years[2] or '') if years else '',
                  chapters=label(r'Series has ([0-9]+) chapters?'), volumes=label(r'Series has ([0-9]+) volumes?'),
                  source_url=canonical_target.url, html_path=html_path, html_sha256=hashlib.sha256(raw).hexdigest())
    if scalar['description'] == 'No description available for this series.':
        scalar['description'] = ''
    result = Manga(scalar)
    # Only language divs in the primary header, never titles in recommendation cards.
    header_end = meta.find('</header>')
    header = meta[:header_end] if header_end >= 0 else meta[:meta.find('<nav')]
    seen_titles = {title}
    for match in LANG_TITLE_RE.finditer(header):
        name = clean_text(match[2])
        if name and name not in seen_titles:
            seen_titles.add(name)
            result.titles.append(dict(manga_id=target.manga_id, title=name, language=match[1], origin='regex:header-lang'))
    auxiliary = matching_jsonld(text, target.manga_id)
    alternatives = auxiliary.get('alternateName', [])
    if isinstance(alternatives, str):
        alternatives = [alternatives]
    if not isinstance(alternatives, list):
        alternatives = []
    for name in alternatives:
        if isinstance(name, str) and name.strip() and name not in seen_titles:
            seen_titles.add(name)
            result.titles.append(dict(manga_id=target.manga_id, title=unescape(name), language='', origin='jsonld:alternateName'))
    role_names = {}
    for key, role in [('author', 'author'), ('illustrator', 'illustrator')]:
        people = auxiliary.get(key, [])
        if isinstance(people, (dict, str)):
            people = [people]
        if not isinstance(people, list):
            people = []
        for person in people:
            name = person.get('name') if isinstance(person, dict) else person
            if isinstance(name, str):
                role_names.setdefault(name, []).append(role)
    names = []
    for link in ANCHOR_RE.finditer(meta):
        a = attrs(link[1])
        if re.match(r'^/search\?staff=', a.get('href', '')) and 'link-chip' in a.get('class', '').split():
            names.append(clean_text(link[2]))
    for name in unique(names):
        for role in role_names.get(name, ['unknown']):
            result.creators.append(dict(manga_id=target.manga_id, name=name, role=role,
                origin='regex:staff-link+jsonld:role' if name in role_names else 'regex:staff-link'))
    # Restrict to primary Tags, ending before the recommendation rails.
    tag_start = re.search(r'<h2\b[^>]*>\s*Tags\s*</h2>', dom, re.I)
    if tag_start:
        tags = dom[tag_start.end():]
        stop = re.search(r'<h[2-6]\b[^>]*>\s*(?:Similar series|Readers also like)\b', tags, re.I)
        if stop:
            tags = tags[:stop.start()]
        seen = set()
        for tag in OPEN_TAG_RE.findall(tags):
            a = attrs(tag)
            if 'data-tag-id' not in a:
                continue
            name = re.sub(r',\s*[A-Za-z -]+ weight(?:,\s*spoiler)?$', '', a.get('aria-label', ''), flags=re.I).strip()
            kind = 'genre' if '/genre/' in a.get('href', '') else 'theme'
            key = (a['data-tag-id'], kind)
            if name and key not in seen:
                seen.add(key)
                result.tags.append(dict(manga_id=target.manga_id, name=name, kind=kind, tag_id=a['data-tag-id'], origin='regex:tag-aria'))
    return result


def extract_dataset(input_dir, output_dir, *, file=None, url=None):
    root, out = Path(input_dir), Path(output_dir)
    errors, candidates = [], {}
    if file:
        path = Path(file)
        raw = path.read_bytes()
        if not url:
            text = raw.decode('utf-8', errors='replace')
            url = next((attrs(t).get('href') for t in OPEN_TAG_RE.findall(text)
                        if t.lower().startswith('<link') and attrs(t).get('rel') == 'canonical'), '')
        candidates[str(path)] = (path, url, None)
    else:
        # Successful audit rows are authoritative; do not index arbitrary orphan HTML.
        for row in read_tsv(root / 'crawl_metadata.tsv'):
            if row['manga_id'] and row['html_path'] and not row['error'] and row['http_status'] == '200':
                candidates[row['manga_id']] = (root / row['html_path'], row['final_url'], row)
    extracted = {}
    for path, source_url, metadata in candidates.values():
        try:
            raw = path.read_bytes()
            if metadata and (len(raw) != int(metadata['saved_bytes']) or hashlib.sha256(raw).hexdigest() != metadata['sha256']):
                raise ValueError('html_integrity_mismatch')
            item = extract_html(raw, source_url, str(path))
            if item.scalar['manga_id'] in extracted:
                raise ValueError('duplicate_manga_id')
            extracted[item.scalar['manga_id']] = item
        except (OSError, ValueError, UnicodeError) as error:
            errors.append(dict(html_path=str(path), source_url=source_url, error=str(error)))
    ordered = [extracted[key] for key in sorted(extracted, key=int)]
    write_tsv(out / 'manga.tsv', MANGA_FIELDS, (item.scalar for item in ordered))
    for name, fields, attribute in [('titles', TITLE_FIELDS, 'titles'), ('creators', CREATOR_FIELDS, 'creators'), ('tags', TAG_FIELDS, 'tags')]:
        write_tsv(out / (name + '.tsv'), fields, (row for item in ordered for row in getattr(item, attribute)))
    write_tsv(out / 'extraction_errors.tsv', ERROR_FIELDS, errors)
    coverage = []
    for field_name in MANGA_FIELDS:
        filled = sum(bool(item.scalar[field_name]) for item in ordered)
        coverage.append(dict(field=field_name, filled=filled, total=len(ordered)))
    for field_name in ['titles', 'creators', 'genres', 'themes']:
        filled = sum(bool(getattr(item, field_name)) if field_name in {'titles', 'creators'} else
                     any(t['kind'] == ('genre' if field_name == 'genres' else 'theme') for t in item.tags) for item in ordered)
        coverage.append(dict(field=field_name, filled=filled, total=len(ordered)))
    write_tsv(out / 'field_coverage.tsv', ['field', 'filled', 'total'], coverage)
    return {'candidates': len(candidates), 'extracted': len(ordered), 'errors': len(errors)}
