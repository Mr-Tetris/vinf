import tempfile
import unittest
import gzip
import hashlib
from pathlib import Path

from manga_ir.extract import clean_text, extract_dataset, extract_html
from crawler.storage import read_tsv


class ExtractTests(unittest.TestCase):
    def test_twenty_independently_curated_reference_pages(self):
        root = Path(__file__).parent / 'fixtures'
        references = read_tsv(root / 'reference.tsv')
        self.assertGreaterEqual(len(references), 20)
        self.assertEqual(len({row['manga_id'] for row in references}), len(references))
        for expected in references:
            with self.subTest(manga_id=expected['manga_id']):
                raw = gzip.decompress((root / expected['fixture']).read_bytes())
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected['html_sha256'])
                item = extract_html(raw, expected['source_url'], expected['fixture'])
                for field in ['manga_id', 'title', 'work_type', 'status', 'start_year', 'end_year', 'chapters', 'volumes']:
                    self.assertEqual(item.scalar[field], expected[field], field)
                for field, role in [('authors', 'author'), ('illustrators', 'illustrator')]:
                    self.assertEqual([r['name'] for r in item.creators if r['role'] == role], expected[field].split('|') if expected[field] else [])
                if expected['ja_title']:
                    self.assertIn(expected['ja_title'], [r['title'] for r in item.titles])
                self.assertIn(expected['genre_contains'], [r['name'] for r in item.tags if r['kind'] == 'genre'])
                if expected['description_missing'] == '1':
                    self.assertEqual(item.scalar['description'], '')
                else:
                    self.assertIn(expected['description_contains'], item.scalar['description'])

    def test_real_existing_page_fields_and_main_series_scope(self):
        fixture = Path(__file__).parent / 'fixtures' / 'pages' / '676.html.gz'
        item = extract_html(gzip.decompress(fixture.read_bytes()),
                            'https://mangabaka.org/manga/676/Ichi-the-Witch', str(fixture))
        self.assertEqual(item.scalar['title'], 'Ichi the Witch')
        self.assertEqual(item.scalar['chapters'], '101')
        self.assertEqual(item.scalar['end_year'], '')
        self.assertIn(('Osamu Nishi', 'author'), [(r['name'], r['role']) for r in item.creators])
        self.assertIn(('Shiro Usazaki', 'illustrator'), [(r['name'], r['role']) for r in item.creators])
        self.assertIn('魔男のイチ', [r['title'] for r in item.titles])

    def test_cleaning_unicode_boundaries_and_entities(self):
        self.assertEqual(clean_text('<p>A&amp;B</p><p>魔男<br>one</p>'), 'A&B 魔男 one')

    def test_reject_list_challenge_and_wrong_canonical(self):
        for raw, url in [(b'<h1>x</h1>', 'https://mangabaka.org/popular'),
                         (b'<title>Just a moment</title>', 'https://mangabaka.org/manga/1/A'),
                         (b'<link rel="canonical" href="https://mangabaka.org/manga/2/B"><h1>A</h1>', 'https://mangabaka.org/manga/1/A')]:
            with self.assertRaises(ValueError):
                extract_html(raw, url, 'example.html')

    def test_content_preference_gate_is_not_a_manga_document(self):
        raw = b'<title>A | MangaBaka</title><h1>A</h1><h2>Filtered Content</h2>'
        with self.assertRaisesRegex(ValueError, 'content_filtered'):
            extract_html(raw, 'https://mangabaka.org/manga/1/A', 'a.html')

    def test_missing_fields_and_unknown_creator_role(self):
        raw = b'''<title>A | MangaBaka</title><link rel="canonical" href="https://mangabaka.org/manga/1/A">
        <header><h1>A</h1></header><a class="link-chip" href="/search?staff=X"><span>X</span></a>
        <section aria-label="Description"><p>A &amp; B</p></section><h2>Similar series</h2>
        <h1>Wrong</h1><a aria-label="Published 1900">Wrong</a>'''
        item = extract_html(raw, 'https://mangabaka.org/manga/1/A', 'a.html')
        self.assertEqual(item.scalar['title'], 'A')
        self.assertEqual(item.scalar['volumes'], '')
        self.assertEqual(item.scalar['start_year'], '')
        self.assertEqual(item.creators[0]['role'], 'unknown')
        self.assertEqual(item.scalar['description'], 'A & B')

    def test_primary_tags_exclude_recommendations_and_ui_weight(self):
        raw = b'''<title>A | MangaBaka</title><link rel="canonical" href="https://mangabaka.org/manga/1/A">
        <header><h1>A</h1></header><section aria-label="Description"><p>Primary</p></section><h2>Tags</h2>
        <a href="/manga/genre/1/Drama" data-tag-id="1" aria-label="Drama, Core weight, spoiler">Drama</a>
        <h2>Similar series</h2><a href="/manga/genre/2/Wrong" data-tag-id="2" aria-label="Wrong, Core weight">Wrong</a>'''
        item = extract_html(raw, 'https://mangabaka.org/manga/1/A', 'a.html')
        self.assertEqual([r['name'] for r in item.tags], ['Drama'])

    def test_single_file_cli_path_preserves_original_html(self):
        root = Path(__file__).parent / 'fixtures'
        raw = gzip.decompress((root / 'pages/676.html.gz').read_bytes())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'source.html'
            path.write_bytes(raw)
            result = extract_dataset(temporary, Path(temporary) / 'out', file=path)
            self.assertEqual(result['extracted'], 1)
            self.assertEqual(path.read_bytes(), raw)
