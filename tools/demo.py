"""Reproduce the full offline pipeline using the 20 packaged historical snapshots."""

import argparse
import gzip
import hashlib
from pathlib import Path

from crawler.storage import METADATA_FIELDS, read_tsv, write_tsv


def assemble_demo(destination):
    destination = Path(destination)
    fixtures = Path(__file__).resolve().parents[1] / 'tests/fixtures'
    if (destination / 'crawl_metadata.tsv').exists():
        raise ValueError('Demo výstup už existuje; použite nový adresár.')
    references = read_tsv(fixtures / 'reference.tsv')
    metadata = read_tsv(fixtures / 'source_metadata.tsv')
    for row in references:
        raw = gzip.decompress((fixtures / row['fixture']).read_bytes())
        if hashlib.sha256(raw).hexdigest() != row['html_sha256']:
            raise ValueError('Poškodená fixture ' + row['fixture'])
        path = destination / 'html' / ('manga_' + row['manga_id'] + '.html')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    # These are copied ORIGINAL HTTP records, not simulated/new network attempts.
    write_tsv(destination / 'crawl_metadata.tsv', METADATA_FIELDS, metadata)
    write_tsv(destination / 'data_origin.tsv', ['name', 'value'], [
        {'name': 'origin', 'value': '20 packaged original historical HTML snapshots and original audit subset; no new HTTP requests'},
        {'name': 'scope', 'value': 'fixture-only subset; robots/list/sitemap requests and the rest of the crawl are not included'}])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, default=Path('data/demo'))
    p.add_argument('--tiktoken')
    args = p.parse_args(argv)
    assemble_demo(args.output_dir)
    from manga_ir.extract import extract_dataset
    from manga_ir.index import build_index
    from manga_ir.stats import collect_stats
    from manga_ir.cli import main as pipeline
    print(extract_dataset(args.output_dir, args.output_dir / 'extracted'))
    print(build_index(args.output_dir / 'extracted', args.output_dir / 'index'))
    pipeline(['compare', '--index-dir', str(args.output_dir / 'index'), '--output', str(args.output_dir / 'query_comparison.tsv')])
    print(collect_stats(args.output_dir, args.output_dir / 'extracted', args.output_dir / 'index', args.output_dir / 'reports', args.tiktoken))


if __name__ == '__main__':
    main()
