"""Local crawl/extraction/index statistics and optional cache-only tiktoken counts."""

import hashlib
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

from crawler.storage import Storage, read_tsv, statistics, write_tsv
from .index import Index, file_hash
from crawler.validation import blocked_document


def count_bonus(documents, encoding_name):
    try:
        import tiktoken
        import tiktoken.load
        # read_file_cached uses read_file only on a cache miss. Fail closed there:
        # this local stats command must never download encoding data implicitly.
        with patch('tiktoken.load.read_file', side_effect=RuntimeError('Encoding nie je lokálne v cache; spustite prepare-tokenizer.')):
            encoding = tiktoken.get_encoding(encoding_name)
        rows = []
        for row in documents.values():
            rows.append(dict(document_id=row['document_id'], manga_id=row['manga_id'], encoding=encoding_name,
                             token_count=len(encoding.encode(row['text'], disallowed_special=())), text_sha256=row['text_sha256']))
        return rows, {'status': 'available', 'version': version('tiktoken'), 'encoding': encoding_name,
                      'documents': len(rows), 'total_tokens': sum(row['token_count'] for row in rows)}
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        return [], {'status': 'unavailable', 'encoding': encoding_name, 'reason': str(error)}


def dataset_manifest(root):
    root = Path(root)
    pending, processed, _ = Storage(root).load()
    metadata = read_tsv(root / 'crawl_metadata.tsv')
    ids = {row['key'].split(':', 1)[1] for row in pending + processed if row['key'].startswith('manga:')}
    acquired = set()
    rejected = set()
    for row in metadata:
        if row['manga_id'] and row['html_path'] and row['http_status'] == '200' and not row['error']:
            path = root / row['html_path']
            if path.is_file() and path.stat().st_size == int(row['saved_bytes']) and file_hash(path) == row['sha256']:
                if blocked_document(path.read_bytes()):
                    rejected.add(row['manga_id'])
                else:
                    acquired.add(row['manga_id'])
    ids |= acquired
    unavailable = {row['key'].split(':', 1)[1] for row in processed if row['key'].startswith('manga:') and row['state'] == 'permanent_failure'}
    aliases = {row['key'].split(':', 1)[1] for row in processed if row['key'].startswith('manga:') and row['state'] == 'duplicate'}
    waiting = {row['key'].split(':', 1)[1] for row in pending if row['key'].startswith('manga:')}
    # Saving HTML/audit precedes the queue checkpoint. Count durable successes
    # as acquired even across that gap; a requeued historical gate is waiting,
    # rather than simultaneously unavailable. Expose raw pending separately.
    sitemap_records = read_tsv(root / 'sitemap_metadata.tsv')
    times = [row['fetched_at_utc'] for row in metadata if row['fetched_at_utc']]
    return {'scope': str(root), 'first_attempt_utc': min(times, default=''), 'last_attempt_utc': max(times, default=''),
            'relevant_document': 'HTTP 200 HTML; primary manga detail /manga/<numeric_id>; canonical ID and title/H1; no recognized challenge or content preference gate',
            'discovered_unique_manga_ids': len(ids), 'acquired_manga_ids': len(acquired),
            'unavailable_manga_ids': len((unavailable | rejected) - acquired - waiting),
            'remaining_manga_ids': len(waiting - acquired), 'duplicate_alias_ids': len(aliases),
            'pending_manga_ids': len(waiting),
            'saved_manga_ids_pending_checkpoint': len(waiting & acquired),
            'discovery_sources': ('packaged original snapshot subset; no network discovery in this demo' if (root / 'data_origin.tsv').exists() else
                                  'legacy stored HTML, explicit seeds, /popular links, declared robots sitemap index and URL sets (when enabled)'),
            'sitemap_files_acquired': len(sitemap_records),
            'sitemap_files_remaining': sum(row['key'].startswith('sitemap:') for row in pending),
            'sitemap_fetches_truncated': sum(row['truncated'] == 'true' for row in sitemap_records),
            'coverage': 'PARTIAL; acquired subset of discovered manga IDs; no complete-domain claim'}


def collect_stats(crawl_dir, extracted_dir, index_dir, output_dir, encoding_name=None):
    crawl = statistics(crawl_dir)
    crawl['rejected_by_type'] = dict(Counter(row['error'] for row in crawl['rejected_documents']))
    extracted_dir, index_dir, output_dir = Path(extracted_dir), Path(index_dir), Path(output_dir)
    extraction = {'records': len(read_tsv(extracted_dir / 'manga.tsv')),
                  'errors': len(read_tsv(extracted_dir / 'extraction_errors.tsv')),
                  'field_coverage': read_tsv(extracted_dir / 'field_coverage.tsv')}
    index = Index(index_dir)
    for name in ['manga.tsv', 'titles.tsv', 'creators.tsv', 'tags.tsv']:
        if file_hash(extracted_dir / name) != index.metadata['input:' + name]:
            raise ValueError('Index je zastaraný voči extrakcii; znovu spustite index.')
    index_stats = {'N': len(index.documents), 'terms': len(index.terms),
                   'postings': sum(len(v) for v in index.postings.values()),
                   'tokens': sum(int(row['token_count']) for row in index.documents.values()),
                   'skipped_empty': int(index.metadata['skipped_empty']),
                   'files_bytes': sum((index_dir / name).stat().st_size for name in
                       ['documents.tsv', 'terms.tsv', 'postings.tsv', 'index_metadata.tsv', 'skipped_documents.tsv'])}
    crawl['html_mib'] = crawl['html_bytes_on_disk'] / 1048576
    bonus_rows, bonus = count_bonus(index.documents, encoding_name) if encoding_name else ([], {'status': 'not_requested'})
    manifest = dataset_manifest(crawl_dir)
    totals = {'crawl': crawl, 'extraction': extraction, 'index': index_stats, 'tiktoken': bonus,
              'scale': {'html_at_least_500_MB_decimal': crawl['html_bytes_on_disk'] >= 500_000_000,
                        'html_at_least_500_MiB': crawl['html_bytes_on_disk'] >= 500 * 1048576,
                        'more_than_one_million_records': extraction['records'] > 1_000_000}}
    rows = []
    def flatten(prefix, value):
        if isinstance(value, dict):
            for key, item in value.items():
                flatten(prefix + '.' + key if prefix else key, item)
        elif not isinstance(value, list):
            rows.append({'name': prefix, 'value': str(value)})
    flatten('', totals)
    write_tsv(output_dir / 'statistics.tsv', ['name', 'value'], rows)
    write_tsv(output_dir / 'dataset_manifest.tsv', ['name', 'value'], ({'name': k, 'value': str(v)} for k, v in manifest.items()))
    write_tsv(output_dir / 'tiktoken_counts.tsv', ['document_id', 'manga_id', 'encoding', 'token_count', 'text_sha256'], bonus_rows)
    write_tsv(output_dir / 'tiktoken_metadata.tsv', ['name', 'value'], ({'name': k, 'value': str(v)} for k, v in bonus.items()))
    return totals
