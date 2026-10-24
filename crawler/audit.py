"""Offline revalidation of historical HTML; never rewrites HTML or HTTP audit rows."""

import hashlib
from pathlib import Path

import requests

from .crawler import validate_document
from .storage import Storage, read_tsv, write_tsv
from .urls import normalize_url


def audit_dataset(root, output, repair_state=False):
    root = Path(root)
    store = Storage(root)
    with store.lock():
        pending, processed, control = store.load()
        latest = {row['html_path']: row for row in read_tsv(root / 'crawl_metadata.tsv') if row['html_path'] and not row['error']}
        rows, changed = [], 0
        by_key = {row['key']: row for row in processed}
        for path, row in latest.items():
            target = normalize_url(row['final_url'])
            error = ''
            try:
                raw = (root / path).read_bytes()
                if len(raw) != int(row['saved_bytes']) or hashlib.sha256(raw).hexdigest() != row['sha256']:
                    error = 'html_integrity_mismatch'
                elif target is None:
                    error = 'outside_scope'
                else:
                    response = requests.Response()
                    response._content = raw
                    response._content_consumed = True
                    response.headers['Content-Type'] = row['content_type']
                    response.encoding = 'utf-8'
                    error = validate_document(response, target)
            except OSError as failure:
                error = 'missing_file: ' + str(failure)
            rows.append(dict(source_url=row['final_url'], manga_id=row['manga_id'], html_path=path,
                             sha256=row['sha256'], usable=str(not error).lower(), error=error))
            if repair_state and error == 'content_filtered' and target and target.key in by_key:
                record = by_key[target.key]
                if (record['state'] == 'success' and record['html_path'] == path
                        and record['sha256'] == row['sha256']):
                    record.update(state='permanent_failure', error=error)
                    changed += 1
        if changed:
            store.checkpoint(pending, list(by_key.values()), control)
        write_tsv(output, ['source_url', 'manga_id', 'html_path', 'sha256', 'usable', 'error'], rows)
    return {'historical_html_files': len(rows), 'usable': sum(row['usable'] == 'true' for row in rows),
            'rejected': sum(row['usable'] != 'true' for row in rows), 'state_reclassified': changed}
