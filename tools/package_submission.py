"""Deterministic allowlisted ZIP; no venv, full crawl, credentials or checkpoint copies."""

import argparse
import hashlib
import zipfile
from pathlib import Path


def package(output):
    root = Path(__file__).resolve().parents[1]
    selected = [root / name for name in ['README.md', '.gitignore', 'requirements.txt', 'requirements-bonus.txt', 'konzultacia_2b_podklady.md']]
    for name in ['crawler', 'manga_ir', 'tests', 'tools', 'examples', 'sample_outputs']:
        for path in (root / name).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix in {'.py', '.md', '.txt', '.tsv', '.gz'}:
                selected.append(path)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(set(selected)):
            name = 'vinf/' + path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 19, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compresslevel=9)
    return {'zip': str(output), 'bytes': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'files': len(set(selected))}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=Path('dist/vinf_2b.zip'))
    print(package(p.parse_args().output))


if __name__ == '__main__':
    main()
