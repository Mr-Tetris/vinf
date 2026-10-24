import unittest

from crawler.urls import ORIGIN, discover_urls, extract_hrefs, normalize_url


class URLTests(unittest.TestCase):
    def test_regex_quotes_and_entities(self):
        document = '''<!-- <a href="/bad"> --><script>let x = '<a href="/bad">';</script>
            <a HREF = '/popular?page=2&amp;x=1'>next</a>
            <a href="/manga/12/King&#39;s-name">title</a>
            <a data-href="/bad" x:href="/bad" href="/popular">ok</a>'''
        self.assertEqual(list(extract_hrefs(document)),
                         ["/popular?page=2&x=1", "/manga/12/King's-name", "/popular"])

    def test_href_text_inside_another_attribute_and_greater_than(self):
        document = '''<a title='href="/bad">text' href="/popular?page=2">next</a>'''
        self.assertEqual(list(extract_hrefs(document)), ["/popular?page=2"])

    def test_relative_and_fragment(self):
        self.assertEqual(normalize_url("../20/Title#description", ORIGIN + "/manga/12/A").url,
                         ORIGIN + "/manga/20/Title")
        self.assertEqual(normalize_url("/manga/0012/A/#fragment").manga_id, "12")

    def test_reject_external_domains_and_unsafe_paths(self):
        for url in ["https://www.mangabaka.org/manga/1/A", "https://mangabaka.org.evil.test/manga/1/A",
                    "//evil.test/manga/1/A", "https://evil.test@mangabaka.org/manga/1/A",
                    "http://mangabaka.org/manga/1/A", "javascript:alert(1)", "mailto:a@example.org",
                    "data:text/html,abc", "https://mangabaka.org:8443/manga/1/A",
                    "/manga/1/A.png", "/manga/1/A/chapters", "/manga/1/covers",
                    "/manga/1/A?tab=covers", "/manga/1/a%2fchapters", "/manga/1/a%252fchapters"]:
            with self.subTest(url=url):
                self.assertIsNone(normalize_url(url))

    def test_deduplicate_by_id(self):
        html = '<a href="/manga/12/A"><a href="/manga/12/Other"><a href="/manga/13/B">'
        self.assertEqual([url.key for url in discover_urls(html, ORIGIN)], ["manga:12", "manga:13"])
        self.assertEqual(normalize_url("/manga/12/A").filename, "manga_12.html")

    def test_verified_pagination_only(self):
        self.assertEqual(normalize_url("/popular?page=002#list").url, ORIGIN + "/popular?page=2")
        self.assertEqual(normalize_url("/popular?page=1").key, normalize_url("/popular").key)
        self.assertIsNotNone(normalize_url("/search"))
        for url in ["/popular?page=0", "/popular?page=501", "/popular?page=2&page=3",
                    "/popular?page=2&sort=rating", "/search?page=2", "/search?q=test", "/popular?q="]:
            self.assertIsNone(normalize_url(url), url)


if __name__ == "__main__":
    unittest.main()
