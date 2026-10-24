"""Rebuild portable full-byte gzip fixtures from the independently curated reference TSV."""

import argparse
import gzip
import hashlib
from pathlib import Path

from crawler.storage import read_tsv


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--crawl-dir', type=Path, default=Path('data'))
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    for row in read_tsv(root / 'tests/fixtures/reference.tsv'):
        original = args.crawl_dir / 'html' / ('manga_' + row['manga_id'] + '.html')
        content = original.read_bytes()
        if hashlib.sha256(content).hexdigest() != row['html_sha256']:
            raise ValueError('Referenčný snapshot sa zmenil: ' + str(original))
        destination = root / 'tests/fixtures' / row['fixture']
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(gzip.compress(content, mtime=0))
    print('Fixtures overené a zostavené; referenčné očakávania sa nemenili.')


if __name__ == '__main__':
    main()
