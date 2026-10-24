import gzip
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler.storage import Storage, read_tsv, write_tsv
from manga_ir.extract import extract_dataset
from manga_ir.index import Index, build_index
from manga_ir.stats import collect_stats, count_bonus, dataset_manifest
from crawler.urls import normalize_url
from tools.demo import assemble_demo


class PipelineTests(unittest.TestCase):
    def test_manifest_separates_durable_success_and_requeued_gates_from_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            store = Storage(root)
            for manga_id, raw in [('1', b'<title>MangaBaka</title><h1>Saved</h1>'),
                                  ('4', b'<title>MangaBaka</title><h2>Filtered Content</h2>')]:
                target = normalize_url('/manga/' + manga_id)
                path, size, digest = store.save_html(target, raw)
                store.append_attempt(dict(requested_url=target.url, final_url=target.url, manga_id=manga_id,
                    http_status=200, html_path=path, saved_bytes=size, sha256=digest))
            pending = [dict(key='manga:' + i, url='https://mangabaka.org/manga/' + i,
                            state='pending', transient_failures='0', not_before_utc='', error='')
                       for i in ['1', '2', '4']]
            failed = dict(key='manga:3', url='https://mangabaka.org/manga/3', final_url='',
                          state='permanent_failure', manga_id='3', html_path='', saved_bytes='0', sha256='', error='http_404')
            store.checkpoint(pending, [failed], {})
            manifest = dataset_manifest(root)
            self.assertEqual(manifest['discovered_unique_manga_ids'], 4)
            self.assertEqual(manifest['acquired_manga_ids'], 1)
            self.assertEqual(manifest['remaining_manga_ids'], 2)
            self.assertEqual(manifest['unavailable_manga_ids'], 1)
            self.assertEqual(manifest['pending_manga_ids'], 3)
            self.assertEqual(manifest['saved_manga_ids_pending_checkpoint'], 1)

    def test_portable_twenty_page_pipeline_no_network(self):
        with tempfile.TemporaryDirectory() as temporary, patch('requests.Session', side_effect=AssertionError('no network')):
            root = Path(temporary)
            assemble_demo(root)
            extracted, index = root / 'extracted', root / 'index'
            self.assertEqual(extract_dataset(root, extracted)['extracted'], 20)
            self.assertEqual(build_index(extracted, index)['N'], 20)
            self.assertTrue(Index(index).search('magic fantasy', 'smooth'))
            stats = collect_stats(root, extracted, index, root / 'reports')
            self.assertEqual(stats['crawl']['manga_pages'], 20)
            self.assertEqual(stats['index']['N'], 20)
            self.assertEqual(stats['tiktoken']['status'], 'not_requested')

    def test_bonus_special_strings_are_ordinary_text_and_missing_library_optional(self):
        class FakeEncoding:
            def encode(self, text, **kwargs):
                self_test.assertEqual(text, 'hello <|endoftext|>')
                self_test.assertEqual(kwargs, {'disallowed_special': ()})
                return [1, 2, 3]
        self_test = self
        try:
            import tiktoken
        except ImportError:
            rows, meta = count_bonus({}, 'cl100k_base')
            self.assertEqual(meta['status'], 'unavailable')
            return
        docs = {'1': dict(document_id='1', manga_id='1', text='hello <|endoftext|>', text_sha256='hash')}
        with patch('tiktoken.get_encoding', return_value=FakeEncoding()):
            rows, meta = count_bonus(docs, 'cl100k_base')
            self.assertEqual(meta['total_tokens'], 3)
        with patch('tiktoken.get_encoding', side_effect=RuntimeError('cache missing')):
            rows, meta = count_bonus(docs, 'cl100k_base')
            self.assertEqual(meta['status'], 'unavailable')
            self.assertEqual(rows, [])

    def test_bonus_cache_miss_cannot_download(self):
        try:
            import tiktoken
            import tiktoken.load
        except ImportError:
            return  # Optional bonus absent is a supported configuration.
        def load_without_cache(name):
            return tiktoken.load.read_file('https://example.invalid/encoding')
        with patch('tiktoken.get_encoding', side_effect=load_without_cache), patch('requests.get', side_effect=AssertionError('network forbidden')):
            rows, meta = count_bonus({}, 'cl100k_base')
            self.assertEqual(meta['status'], 'unavailable')
