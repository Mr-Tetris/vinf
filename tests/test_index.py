import math
import tempfile
import unittest
from pathlib import Path

from crawler.storage import read_tsv, write_tsv
from manga_ir.extract import MANGA_FIELDS, TITLE_FIELDS, CREATOR_FIELDS, TAG_FIELDS
from manga_ir.index import Index, build_index, idfs
from manga_ir.text import compose_text, tokenize


def make_corpus(root, texts):
    root = Path(root)
    write_tsv(root / 'manga.tsv', MANGA_FIELDS,
        ({**dict.fromkeys(MANGA_FIELDS, ''), 'manga_id': str(i), 'title': '', 'description': text,
          'source_url': f'https://mangabaka.org/manga/{i}/A'} for i, text in texts))
    for filename, fields in [('titles', TITLE_FIELDS), ('creators', CREATOR_FIELDS), ('tags', TAG_FIELDS)]:
        write_tsv(root / (filename + '.tsv'), fields, [])


class IndexTests(unittest.TestCase):
    def test_manual_corpus_tf_df_idf_scores_ranking_and_zero_score(self):
        with tempfile.TemporaryDirectory() as root:
            source, out = Path(root) / 'input', Path(root) / 'index'
            make_corpus(source, [(2, 'all cat cat dog'), (10, 'all dog'), (11, 'all bird'), (12, '___')])
            summary = build_index(source, out)
            self.assertEqual(summary['N'], 3)
            self.assertEqual(summary['skipped_empty'], 1)
            index = Index(out)
            self.assertEqual(index.postings['cat'], {'2': 2})
            self.assertEqual(index.terms['dog'][0], 2)
            self.assertAlmostEqual(index.terms['dog'][1], math.log(3 / 2))
            self.assertAlmostEqual(index.terms['cat'][2], math.log(4 / 2) + 1)
            classic = index.search('cat dog', 'classic')
            smooth = index.search('cat dog', 'smooth')
            self.assertEqual([r['manga_id'] for r in classic], ['2', '10'])
            self.assertEqual([r['manga_id'] for r in smooth], ['2', '10'])
            self.assertAlmostEqual(classic[0]['score'], (1 + math.log(2)) * math.log(3) + math.log(3 / 2))
            self.assertAlmostEqual(smooth[0]['score'], (1 + math.log(2)) * (math.log(2) + 1) + math.log(4 / 3) + 1)
            self.assertEqual(index.search('dog dog mystery'), index.search('dog'))
            self.assertEqual(index.search('unknown'), [])
            zero = index.search('all', 'classic')
            self.assertEqual([r['manga_id'] for r in zero], ['2', '10', '11'])
            self.assertTrue(all(r['score'] == 0 for r in zero))
            with self.assertRaisesRegex(ValueError, 'Prázdny'):
                index.search('___ !')
            with self.assertRaises(ValueError):
                idfs(3, 0)

    def test_normalization_and_equal_fields_without_duplicate_title(self):
        self.assertEqual(tokenize('Cafe\u0301 CAFÉ A_B &amp; １２3 魔男のイチ'), ['café', 'café', 'a', 'b', '１２3', '魔男のイチ'])
        text = compose_text({'title': 'A', 'description': 'A cat'},
                            [{'title': 'A'}, {'title': 'a'}, {'title': 'B'}],
                            [{'name': 'Creator'}, {'name': 'Creator'}],
                            [{'name': 'Magic', 'kind': 'genre'}, {'name': 'Magic', 'kind': 'theme'}])
        self.assertEqual(text, 'A\nB\nCreator\nA cat\nMagic')

    def test_corruption_and_incompatible_format_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            make_corpus(Path(root) / 'input', [(1, 'cat')])
            build_index(Path(root) / 'input', Path(root) / 'index')
            path = Path(root) / 'index/postings.tsv'
            path.write_text(path.read_text() + 'cat\t1\t999\n')
            with self.assertRaisesRegex(ValueError, 'Poškodený'):
                Index(path.parent)

    def test_build_is_deterministic(self):
        with tempfile.TemporaryDirectory() as root:
            make_corpus(Path(root) / 'input', [(1, 'a b b'), (2, 'b c')])
            for out in ['first', 'second']:
                build_index(Path(root) / 'input', Path(root) / out)
            for name in ['documents.tsv', 'terms.tsv', 'postings.tsv', 'index_metadata.tsv']:
                self.assertEqual((Path(root) / 'first' / name).read_bytes(), (Path(root) / 'second' / name).read_bytes())

    def test_empty_corpus_and_unsupported_version(self):
        with tempfile.TemporaryDirectory() as root:
            make_corpus(Path(root) / 'input', [(1, '___')])
            build_index(Path(root) / 'input', Path(root) / 'index')
            self.assertEqual(Index(Path(root) / 'index').search('cat'), [])
            path = Path(root) / 'index/index_metadata.tsv'
            rows = read_tsv(path)
            rows[0]['value'] = 'unsupported-format'
            write_tsv(path, ['name', 'value'], rows)
            with self.assertRaisesRegex(ValueError, 'Poškodený'):
                Index(path.parent)
