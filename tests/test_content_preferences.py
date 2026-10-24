import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from crawler.audit import audit_dataset
from crawler.cli import main
from crawler.crawler import Config, content_rating_cookie
from crawler.storage import Storage, read_tsv, statistics
from crawler.urls import ORIGIN, normalize_url
from manga_ir.extract import extract_dataset
from manga_ir.stats import dataset_manifest
from tests import test_crawler as helpers


RATINGS = 'pornographic,safe,suggestive,erotica'
COOKIE = 'pornographic%2Csafe%2Csuggestive%2Cerotica'
URL = ORIGIN + '/manga/12/A'
GATE = b'<title>A | MangaBaka</title><h1>A</h1><h2>Filtered Content</h2>'


class ContentPreferenceTests(unittest.TestCase):
    def build(self, root, actions, **config):
        return helpers.CrawlerTests().build(root, actions, **config)

    def test_cookie_is_encoded_once_scoped_and_optional(self):
        for value in (RATINGS, COOKIE):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as root:
                crawler, session, _ = self.build(root, [], content_ratings=value)
                cookie = next(iter(session.cookies))
                self.assertEqual((cookie.name, cookie.value, cookie.domain, cookie.path, cookie.secure),
                                 ('content_rating', COOKIE, 'mangabaka.org', '/', True))
                request = requests.Request('GET', URL, cookies=session.cookies).prepare()
                self.assertEqual(request.headers['Cookie'], 'content_rating=' + COOKIE)
                outside = requests.Request('GET', 'https://example.org/', cookies=session.cookies).prepare()
                self.assertNotIn('Cookie', outside.headers)
                session.close()
        with tempfile.TemporaryDirectory() as root:
            _, session, _ = self.build(root, [])
            self.assertEqual(len(session.cookies), 0)
        self.assertEqual(content_rating_cookie('safe,safe,suggestive'), 'safe%2Csuggestive')

    def test_invalid_preferences_and_retry_require_explicit_settings(self):
        for value in ('safe,unknown', 'safe;', 'safe,', 'safe\r\nCookie: value', '%252Csafe'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Config(content_ratings=value).validate()
        with self.assertRaises(ValueError):
            Config(retry_content_filtered=True).validate()
        with tempfile.TemporaryDirectory() as root:
            crawler, session, _ = self.build(root, [], content_ratings=RATINGS, retry_content_filtered=True)
            with self.assertRaises(ValueError):
                crawler.run([URL])
            self.assertEqual(session.calls, [])
            self.assertFalse((Path(root) / 'checkpoint.tsv').exists())

    def test_filtered_retry_is_explicit_prioritized_and_keeps_other_failures(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, _, _ = self.build(root, [helpers.robots(),
                lambda u: helpers.response(u, body=GATE), lambda u: helpers.response(u, 404)], max_requests=3)
            crawler.run([URL, ORIGIN + '/manga/13/B', ORIGIN + '/manga/14/C'])
            crawler, session, _ = self.build(root, [helpers.robots()], content_ratings=RATINGS, max_requests=1)
            crawler.run(resume=True)
            self.assertEqual(len(session.calls), 1)  # Cookie alone does not requeue failures.
            crawler, session, _ = self.build(root, [helpers.robots(), lambda u: helpers.response(u)],
                content_ratings=RATINGS, retry_content_filtered=True, max_pages=1)
            result = crawler.run(resume=True)
            self.assertEqual(result['pages_this_run'], 1)
            self.assertEqual([c[0] for c in session.calls], [ORIGIN + '/robots.txt', URL])
            pending, processed, control = Storage(root).load()
            self.assertEqual([row['key'] for row in pending], ['manga:14'])
            by_key = {row['key']: row for row in processed}
            self.assertEqual(by_key['manga:12']['state'], 'success')
            self.assertEqual(by_key['manga:13']['error'], 'http_404')
            self.assertEqual(control['content_rating'], COOKIE)
            self.assertTrue(any(r['method'] == 'content-preference-retry'
                                for r in read_tsv(Path(root) / 'discovery.tsv')))

    def test_historical_gate_retry_survives_restart_and_preserves_audit(self):
        with tempfile.TemporaryDirectory() as root:
            # Emulate a historical gate once mistakenly accepted as a successful HTML.
            store = Storage(root)
            target = normalize_url(URL)
            path, size, digest = store.save_html(target, GATE)
            store.append_attempt(dict(requested_url=URL, final_url=URL, manga_id='12',
                http_status=200, content_type='text/html', html_path=path, saved_bytes=size, sha256=digest))
            store.checkpoint([], [], {})
            crawler, _, _ = self.build(root, [helpers.robots()], max_requests=1,
                content_ratings=RATINGS, retry_content_filtered=True)
            self.assertEqual(crawler.run(resume=True)['reason'], 'request_limit')
            self.assertEqual(Storage(root).load()[0][0]['error'], 'content_filtered')
            # Resuming an already queued retry does not require repeating the retry flag.
            crawler, session, _ = self.build(root, [helpers.robots(), lambda u: helpers.response(u)],
                content_ratings=RATINGS, max_pages=1)
            self.assertEqual(crawler.run(resume=True)['pages_this_run'], 1)
            self.assertEqual(len(session.calls), 2)
            self.assertEqual((Path(root) / path).read_bytes(), GATE)
            for row in read_tsv(Path(root) / 'crawl_metadata.tsv'):
                if row['html_path']:
                    raw = (Path(root) / row['html_path']).read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), row['sha256'])
                    self.assertEqual(len(raw), int(row['saved_bytes']))
            processed = Storage(root).load()[1]
            self.assertNotEqual(processed[0]['html_path'], path)
            report = audit_dataset(root, Path(root) / 'audit.tsv', repair_state=True)
            self.assertEqual(report['state_reclassified'], 0)
            self.assertEqual(statistics(root)['manga_pages'], 1)
            self.assertEqual(extract_dataset(root, Path(root) / 'extracted')['extracted'], 1)
            self.assertEqual(dataset_manifest(root)['unavailable_manga_ids'], 0)
            # Old snapshots cannot undo a later successful download on resume.
            crawler, session, _ = self.build(root, [], content_ratings=RATINGS, retry_content_filtered=True)
            self.assertEqual(crawler.run(resume=True)['reason'], 'queue_exhausted')
            self.assertEqual(session.calls, [])
            self.assertEqual(Storage(root).load()[1][0]['state'], 'success')

    def test_gate_still_rejected_with_cookie_without_retry_loop(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, session, _ = self.build(root, [helpers.robots(), lambda u: helpers.response(u, body=GATE)],
                                            content_ratings=RATINGS)
            result = crawler.run([URL])
            self.assertEqual(result['pages_this_run'], 0)
            self.assertEqual(len(session.calls), 2)
            self.assertEqual(Storage(root).load()[1][0]['error'], 'content_filtered')

    def test_cli_passes_preferences_to_crawler(self):
        with tempfile.TemporaryDirectory() as root:
            with patch('crawler.crawler.Crawler') as factory, patch('crawler.cli.statistics', return_value={}), patch('builtins.print'):
                factory.return_value.run.return_value = {'reason': 'queue_exhausted'}
                result = main(['--output-dir', root, '--resume', '--content-ratings', COOKIE,
                               '--retry-content-filtered', '--max-requests', '2000000'])
                self.assertEqual(result, 0)
                config = factory.call_args.args[0]
                self.assertEqual(config.content_ratings, COOKIE)
                self.assertTrue(config.retry_content_filtered)
                self.assertEqual(config.max_requests, 2000000)
                factory.return_value.run.assert_called_once_with([], resume=True)


if __name__ == '__main__':
    unittest.main()
