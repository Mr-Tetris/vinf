"""One deterministic text composition and tokenizer for indexing and queries."""

import re
import unicodedata
from html import unescape
from itertools import groupby

from crawler.storage import iter_tsv
from .extract import MANGA_FIELDS, TITLE_FIELDS, CREATOR_FIELDS, TAG_FIELDS

TOKEN_RE = re.compile(r'[^\W_]+', re.UNICODE)


def normalize(text):
    return unicodedata.normalize('NFC', unescape(text)).casefold()


def tokenize(text):
    return TOKEN_RE.findall(normalize(text))


def compose_text(scalar, titles=(), creators=(), tags=()):
    """Equal field weight; distinct values once, fixed order, no title repetition."""
    values = [scalar.get('title', '')]
    values += [row['title'] for row in titles]
    values += [row['name'] for row in creators]
    values += [scalar.get('description', '')]
    values += [row['name'] for row in tags if row['kind'] == 'genre']
    values += [row['name'] for row in tags if row['kind'] == 'theme']
    seen, result = set(), []
    for value in values:
        key = normalize(value.strip())
        if key and key not in seen:
            seen.add(key)
            result.append(value.strip())
    return '\n'.join(result)


def iter_records(root):
    """Merge sorted numeric IDs: keep one document's companion rows at a time."""
    from pathlib import Path
    root = Path(root)
    groups = []
    for filename, fields in [('titles.tsv', TITLE_FIELDS), ('creators.tsv', CREATOR_FIELDS), ('tags.tsv', TAG_FIELDS)]:
        def validated(path=root / filename, schema=fields):
            previous = 0
            for row in iter_tsv(path, schema):
                current = int(row['manga_id'])
                if current < previous or current < 1:
                    raise ValueError(f'Nezoradené manga ID v {path}')
                previous = current
                yield row
        iterator = iter(groupby(validated(), key=lambda r: int(r['manga_id'])))
        groups.append([iterator, next(iterator, None)])
    previous = 0
    for scalar in iter_tsv(root / 'manga.tsv', MANGA_FIELDS):
        manga_id = int(scalar['manga_id'])
        if manga_id <= previous:
            raise ValueError('Manga ID musia byť unikátne a numericky zoradené.')
        previous = manga_id
        companions = []
        for state in groups:
            group = state[1]
            if group and group[0] < manga_id:
                raise ValueError('Pomocný TSV odkazuje na neexistujúce manga ID.')
            if group and group[0] == manga_id:
                companions.append(list(group[1]))
                state[1] = next(state[0], None)
            else:
                companions.append([])
        yield scalar, *companions
    if any(state[1] is not None for state in groups):
        raise ValueError('Pomocný TSV obsahuje osirelé manga ID.')
