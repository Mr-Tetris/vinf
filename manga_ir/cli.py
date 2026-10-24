"""Offline pipeline entry point: python -m manga_ir.cli --help."""

import argparse
import csv
import json
import sys
from pathlib import Path

from crawler.storage import write_tsv


def parser():
    p = argparse.ArgumentParser(description='Manga IR: REGEX extrakcia, vlastný index a vyhľadávanie.')
    sub = p.add_subparsers(dest='command', required=True)
    extract = sub.add_parser('extract')
    extract.add_argument('--input-dir', type=Path, default=Path('data'))
    extract.add_argument('--output-dir', type=Path, default=Path('data/extracted'))
    extract.add_argument('--file', type=Path)
    extract.add_argument('--url')
    index = sub.add_parser('index')
    index.add_argument('--input-dir', type=Path, default=Path('data/extracted'))
    index.add_argument('--output-dir', type=Path, default=Path('data/index'))
    search = sub.add_parser('search')
    search.add_argument('--index-dir', type=Path, default=Path('data/index'))
    search.add_argument('--query', required=True)
    search.add_argument('--idf', choices=['classic', 'smooth'], default='classic')
    search.add_argument('--top-k', type=int, default=10)
    compare = sub.add_parser('compare')
    compare.add_argument('--index-dir', type=Path, default=Path('data/index'))
    compare.add_argument('--queries-file', type=Path, default=Path('examples/queries.txt'))
    compare.add_argument('--output', type=Path, default=Path('data/query_comparison.tsv'))
    compare.add_argument('--top-k', type=int, default=10)
    stats = sub.add_parser('stats')
    stats.add_argument('--crawl-dir', type=Path, default=Path('data'))
    stats.add_argument('--extracted-dir', type=Path, default=Path('data/extracted'))
    stats.add_argument('--index-dir', type=Path, default=Path('data/index'))
    stats.add_argument('--output-dir', type=Path, default=Path('data/reports'))
    stats.add_argument('--tiktoken', metavar='ENCODING', help='Voliteľne iba z lokálnej cache, napr. cl100k_base.')
    prepare = sub.add_parser('prepare-tokenizer', help='Explicitne dovolí stiahnutie tiktoken encoding do cache.')
    prepare.add_argument('--encoding', default='cl100k_base')
    audit = sub.add_parser('audit', help='Lokálne znovu overiť HTML vrátane historických content gates.')
    audit.add_argument('--crawl-dir', type=Path, default=Path('data'))
    audit.add_argument('--output', type=Path, default=Path('data/reports/html_validation.tsv'))
    audit.add_argument('--repair-state', action='store_true', help='Historické success content gates preklasifikovať; audit/HTML sa nemení.')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'extract':
            from .extract import extract_dataset
            result = extract_dataset(args.input_dir, args.output_dir, file=args.file, url=args.url)
        elif args.command == 'index':
            from .index import build_index
            result = build_index(args.input_dir, args.output_dir)
        elif args.command in {'search', 'compare'}:
            from .index import Index
            index = Index(args.index_dir)  # loaded once; no index rebuilding per query
            if args.top_k < 1:
                raise ValueError('top-k musí byť kladné.')
            if args.command == 'search':
                rows = index.search(args.query, args.idf, args.top_k)
                if not rows:
                    print('Žiadne známe termy dopytu sa v indexe nenašli.', file=sys.stderr)
                writer = csv.DictWriter(sys.stdout, fieldnames=['rank', 'manga_id', 'title', 'score', 'source_url'], delimiter='\t')
                writer.writeheader()
                writer.writerows(rows)
                return 0
            queries = [line.strip() for line in args.queries_file.read_text(encoding='utf-8').splitlines()
                       if line.strip() and not line.lstrip().startswith('#')]
            rows = [dict(query=q, method=m, **row) for q in queries for m in ['classic', 'smooth']
                    for row in index.search(q, m, args.top_k)]
            write_tsv(args.output, ['query', 'method', 'rank', 'manga_id', 'title', 'score', 'source_url'], rows)
            result = {'queries': len(queries), 'rows': len(rows), 'output': str(args.output)}
        elif args.command == 'stats':
            from .stats import collect_stats
            result = collect_stats(args.crawl_dir, args.extracted_dir, args.index_dir, args.output_dir, args.tiktoken)
        elif args.command == 'audit':
            from crawler.audit import audit_dataset
            result = audit_dataset(args.crawl_dir, args.output, args.repair_state)
        else:
            try:
                import tiktoken
            except ImportError as error:
                raise ValueError('Nainštalujte requirements-bonus.txt; hlavná pipeline tiktoken nepotrebuje.') from error
            encoding = tiktoken.get_encoding(args.encoding)
            result = {'encoding': encoding.name, 'status': 'cache_prepared'}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, UnicodeError) as error:
        print('Chyba: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
