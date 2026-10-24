import gzip
import tempfile
import unittest

from crawler.sitemaps import declared_sitemaps, parse_sitemap, sitemap_url
from crawler.storage import Storage, read_tsv, statistics
from tests import test_crawler as helpers
from tests.test_crawler import response, robots


class SitemapTests(unittest.TestCase):
    def test_index_urlset_gzip_filter_and_truncation(self):
        index = b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://mangabaka.org/sitemap-manga.xml.gz</loc></sitemap><sitemap><loc>https://evil.test/a.xml</loc></sitemap></sitemapindex>'
        parsed = parse_sitemap(index, 'https://mangabaka.org/sitemap.xml')
        self.assertEqual(parsed.kind, 'sitemapindex')
        self.assertEqual(parsed.entries, 2)
        self.assertEqual(len(parsed.links), 1)
        xml = b'<urlset><url><loc>https://mangabaka.org/manga/1/A</loc></url><url><loc>https://mangabaka.org/manga/1/Other</loc></url><url><loc>https://mangabaka.org/manga/2/B</loc></url><url><loc>https://mangabaka.org/search</loc></url></urlset>'
        result = parse_sitemap(gzip.compress(xml), 'https://mangabaka.org/a.xml', 1)
        self.assertEqual([r.manga_id for r in result.links], ['1'])
        self.assertTrue(result.truncated)
        self.assertEqual(len(list(declared_sitemaps('Sitemap: https://mangabaka.org/sitemap.xml\nSitemap: https://evil.test/x.xml'))), 1)

    def test_invalid_xml_and_html_are_rejected(self):
        for raw in [b'<html>error</html>', b'<!DOCTYPE a [<!ENTITY x "x">]><urlset/>', b'<urlset>', b'\x1f\x8bbad gzip']:
            with self.assertRaises(ValueError):
                parse_sitemap(raw, 'https://mangabaka.org/sitemap.xml')

    def test_sitemap_attempts_count_but_are_not_html_documents(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, session, _ = helpers.CrawlerTests().build(root, [
                robots(b'User-agent: *\nDisallow: /manga/2\nSitemap: https://mangabaka.org/sitemap.xml'),
                lambda u: response(u, body=b'<urlset><url><loc>https://mangabaka.org/manga/1/A</loc></url><url><loc>https://mangabaka.org/manga/2/B</loc></url></urlset>', headers={'Content-Type': 'application/xml'}),
                lambda u: response(u)], use_sitemaps=True, details_only=True, max_pages=1)
            result = crawler.run(['https://mangabaka.org/popular'])
            self.assertEqual(result['requests_this_run'], 3)
            self.assertEqual(statistics(root)['unique_pages'], 1)
            self.assertEqual(statistics(root)['manga_pages'], 1)
            self.assertNotIn('https://mangabaka.org/manga/2/B', [c[0] for c in session.calls])
            self.assertTrue(any(r['key'].startswith('sitemap:') and r['state'] == 'sitemap_success' for r in Storage(root).load()[1]))
