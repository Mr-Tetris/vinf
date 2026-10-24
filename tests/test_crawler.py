import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from crawler.cli import main
from crawler.crawler import Config, Crawler
from crawler.storage import Storage, read_tsv, statistics
from crawler.urls import ORIGIN
from tests.test_robots import FakeClock


def response(url, status=200, body=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result.headers.update({"Content-Type": "text/html; charset=utf-8"})
    result.encoding = "utf-8"
    if body is None:
        body = f'<html><head><title>Test Series | MangaBaka</title><link rel="canonical" href="{url}"></head><body><h1>Test Series</h1></body></html>'.encode()
    result._content = body
    result._content_consumed = True
    result.headers.update(headers or {})
    return result


def robots(body=b"User-agent: *\nDisallow: /search\nAllow: /", status=200, headers=None):
    return response(ORIGIN + "/robots.txt", status, body, {"Content-Type": "text/plain", **(headers or {})})


class FakeSession:
    def __init__(self, actions, clock):
        self.actions = iter(actions)
        self.clock = clock
        self.calls = []
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.closed = False

    def mount(self, *args):
        pass

    def get(self, url, **kwargs):
        self.calls.append((url, self.clock.now, kwargs))
        action = next(self.actions)
        if isinstance(action, BaseException):
            raise action
        return action(url) if callable(action) else action

    def close(self):
        self.closed = True


class CrawlerTests(unittest.TestCase):
    def build(self, root, actions, **config):
        clock = FakeClock()
        session = FakeSession(actions, clock)
        crawler = Crawler(Config(output_dir=root, **config), session=session, limiter=clock.limiter())
        return crawler, session, clock

    def test_success_discovery_id_duplicates_and_robots_denial(self):
        with tempfile.TemporaryDirectory() as root:
            html = b'<html><title>Series | MangaBaka</title><h1>Series</h1><a href="/manga/12/Other"><a href="/manga/13/B"><a href="/popular?page=2"><a href="https://external.test/manga/14/C"></html>'
            crawler, session, _ = self.build(root, [robots(), lambda url: response(url, body=html)], max_pages=1)
            result = crawler.run([ORIGIN + "/search", ORIGIN + "/manga/12/A", ORIGIN + "/manga/12/Other"])
            self.assertEqual(result["pages_this_run"], 1)
            self.assertEqual(result["requests_this_run"], 2)
            self.assertEqual([call[0] for call in session.calls], [ORIGIN + "/robots.txt", ORIGIN + "/manga/12/A"])
            pending, processed, _ = Storage(root).load()
            self.assertEqual([row["key"] for row in pending], ["manga:13", "list:" + ORIGIN + "/popular?page=2"])
            self.assertEqual(processed[0]["error"], "robots_disallowed")
            self.assertEqual(statistics(root)["unique_pages"], 1)
            self.assertEqual((Path(root) / "html/manga_12.html").read_bytes(), html)
            self.assertTrue(all(not call[2]["allow_redirects"] for call in session.calls))

    def test_timeout_and_5xx_have_only_two_additional_attempts(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, session, _ = self.build(root, [robots(), requests.Timeout("timed out"),
                                                   lambda url: response(url, 503), requests.ConnectionError("offline")])
            result = crawler.run([ORIGIN + "/manga/12/A"])
            self.assertEqual(result["reason"], "temporary_failures_remaining")
            self.assertEqual(len(session.calls), 4)
            starts = [call[1] for call in session.calls]
            self.assertGreaterEqual(starts[2] - starts[1], 10)
            self.assertGreaterEqual(starts[3] - starts[2], 30)
            rows = read_tsv(Path(root) / "crawl_metadata.tsv")
            self.assertEqual(rows[1]["http_status"], "")
            self.assertEqual(rows[2]["http_status"], "503")
            self.assertEqual(Storage(root).load()[0][0]["transient_failures"], "3")

    def test_404_never_retries_and_repeated_403_pauses(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, session, _ = self.build(root, [robots(), lambda u: response(u, 404),
                                                   lambda u: response(u, 403), lambda u: response(u, 403)])
            result = crawler.run([ORIGIN + f"/manga/{i}/A" for i in range(12, 16)])
            self.assertEqual(result["reason"], "paused: http_403")
            self.assertEqual(len(session.calls), 4)
            self.assertEqual(len(Storage(root).load()[0]), 1)
            self.assertEqual(statistics(root)["errors"], {"HTTP 403": 2, "HTTP 404": 1})

    def test_429_retries_then_pauses_and_preserves_cooldown(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, session, _ = self.build(root, [robots(), lambda u: response(u, 429),
                                                   lambda u: response(u, 429, headers={"Retry-After": "90"})])
            result = crawler.run([ORIGIN + "/manga/12/A"])
            self.assertEqual(result["reason"], "paused: http_429")
            self.assertGreaterEqual(session.calls[2][1] - session.calls[1][1], 60)
            pending, _, control = Storage(root).load()
            self.assertEqual(pending[0]["state"], "temporary_failure")
            self.assertEqual(control["rate_limited_count"], "2")
            self.assertEqual(pending[0]["not_before_utc"], control["cooldown_until_utc"])

    def test_robots_unavailable_fails_closed_missing_is_allowed(self):
        for status in (401, 403, 404, 410):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as root:
                actions = [robots(status=status)]
                if status in (404, 410):
                    actions.append(lambda u: response(u))
                crawler, session, _ = self.build(root, actions, max_pages=1)
                result = crawler.run([ORIGIN + "/manga/12/A"])
                self.assertEqual(result["pages_this_run"], int(status in (404, 410)))
                self.assertEqual(len(session.calls), 2 if status in (404, 410) else 1)

    def test_robots_timeout_does_not_allow_pages(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, session, _ = self.build(root, [requests.Timeout("offline") for _ in range(3)])
            result = crawler.run([ORIGIN + "/manga/12/A"])
            self.assertTrue(result["reason"].startswith("robots_unavailable"))
            self.assertEqual(len(session.calls), 3)
            self.assertEqual(len(Storage(root).load()[0]), 1)

    def test_redirect_origin_path_robots_and_loop_gates(self):
        for location in ("https://external.test/manga/12/A", "/auth", "/search", "/manga/12/A"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as root:
                crawler, session, _ = self.build(root, [robots(), lambda u: response(u, 302, headers={"Location": location})])
                result = crawler.run([ORIGIN + "/manga/12/A"])
                self.assertEqual(len(session.calls), 2)
                self.assertEqual(result["pages_this_run"], 0)

    def test_allowed_redirect_is_counted_paced_and_saved_by_id(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, session, _ = self.build(root, [robots(), lambda u: response(u, 301, headers={"Location": "/manga/12/New"}),
                                                   lambda u: response(u)], max_pages=1)
            result = crawler.run([ORIGIN + "/manga/12/Old"])
            self.assertEqual(result["requests_this_run"], 3)
            self.assertEqual(result["pages_this_run"], 1)
            self.assertGreaterEqual(session.calls[2][1] - session.calls[1][1], 3)
            self.assertTrue((Path(root) / "html/manga_12.html").exists())

    def test_challenge_with_http_200_is_not_a_document(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, _, _ = self.build(root, [robots(), lambda u: response(u, body=b'<html><title>Just a moment...</title></html>')])
            result = crawler.run([ORIGIN + "/manga/12/A"])
            self.assertEqual(result["reason"], "paused: challenge_page")
            self.assertEqual(statistics(root)["unique_pages"], 0)
            self.assertEqual(list((Path(root) / "html").iterdir()), [])

    def test_ctrl_c_and_resume_preserve_active_url(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, _, _ = self.build(root, [robots(), KeyboardInterrupt()])
            result = crawler.run([ORIGIN + "/manga/12/A"])
            self.assertEqual(result["reason"], "interrupted")
            self.assertEqual(len(Storage(root).load()[0]), 1)
            crawler, session, _ = self.build(root, [robots(), lambda u: response(u)], max_pages=1)
            result = crawler.run(resume=True)
            self.assertEqual(result["pages_this_run"], 1)
            self.assertEqual(len(session.calls), 2)
            self.assertEqual(statistics(root)["network_attempts"], 4)
            self.assertEqual(Storage(root).load()[0], [])

    def test_request_limit_includes_robots_and_resume_continues(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, session, _ = self.build(root, [robots()], max_requests=1)
            result = crawler.run([ORIGIN + "/manga/12/A"])
            self.assertEqual(result["reason"], "request_limit")
            self.assertEqual(len(session.calls), 1)
            crawler, _, _ = self.build(root, [robots(), lambda u: response(u)], max_requests=2, max_pages=1)
            result = crawler.run(resume=True)
            self.assertEqual(result["pages_this_run"], 1)

    def test_existing_state_requires_resume_and_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, _, _ = self.build(root, [robots()], max_requests=1)
            crawler.run([ORIGIN + "/manga/12/A"])
            before = Storage(root).load()
            crawler, _, _ = self.build(root, [])
            with self.assertRaises(ValueError):
                crawler.run([ORIGIN + "/manga/13/B"])
            self.assertEqual(Storage(root).load(), before)

    def test_stats_never_use_network(self):
        with tempfile.TemporaryDirectory() as root:
            with patch("requests.Session", side_effect=AssertionError("network forbidden")):
                with patch("builtins.print"):
                    self.assertEqual(main(["--stats", "--output-dir", root]), 0)

    def test_resume_recovers_saved_html_and_links_across_commit_gaps(self):
        for already_processed in (False, True):
            with self.subTest(already_processed=already_processed), tempfile.TemporaryDirectory() as root:
                body = b'<html><title>A | MangaBaka</title><h1>A</h1><a href="/manga/13/B"></html>'
                crawler, _, _ = self.build(root, [robots(), lambda u: response(u, body=body)], max_pages=1)
                crawler.run([ORIGIN + "/manga/12/A"])
                store = Storage(root)
                _, processed, control = store.load()
                # Emulate interruption after durable successful metadata or after
                # completion but before discovery. Both must recover B locally.
                pending = [] if already_processed else [{"key": "manga:12", "url": ORIGIN + "/manga/12/A",
                    "state": "pending", "transient_failures": "0", "not_before_utc": "", "error": ""}]
                store.checkpoint(pending, processed if already_processed else [], control)
                crawler, session, _ = self.build(root, [robots(), lambda u: response(u)], max_pages=1)
                result = crawler.run(resume=True)
                self.assertEqual(result["pages_this_run"], 1)
                self.assertEqual([call[0] for call in session.calls], [ORIGIN + "/robots.txt", ORIGIN + "/manga/13/B"])
                self.assertEqual(statistics(root)["manga_pages"], 2)

    def test_retry_budget_survives_request_limit_and_resume(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, _, _ = self.build(root, [robots(), requests.Timeout("offline")], max_requests=2)
            result = crawler.run([ORIGIN + "/manga/12/A"])
            self.assertEqual(result["reason"], "request_limit")
            self.assertEqual(Storage(root).load()[0][0]["transient_failures"], "1")
            crawler, session, _ = self.build(root, [robots(), lambda u: response(u, 503), lambda u: response(u, 503)])
            result = crawler.run(resume=True)
            self.assertEqual(result["reason"], "temporary_failures_remaining")
            self.assertEqual(len(session.calls), 3)
            # Restored UTC deadlines can differ by float roundoff (<1 ns).
            self.assertGreaterEqual(session.calls[2][1] - session.calls[1][1] + 1e-9, 30)
            self.assertEqual(Storage(root).load()[0][0]["transient_failures"], "3")

    def test_resume_honors_persisted_429_cooldown_before_robots(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, _, _ = self.build(root, [robots(), lambda u: response(u, 429, headers={"Retry-After": "0"}),
                                                   lambda u: response(u, 429, headers={"Retry-After": "90"})])
            crawler.run([ORIGIN + "/manga/12/A"])
            crawler, session, _ = self.build(root, [robots(), lambda u: response(u, 429)])
            result = crawler.run(resume=True)
            self.assertEqual(result["reason"], "paused: http_429")
            self.assertGreater(session.calls[0][1], 85)

    def test_invalid_robots_response_is_not_permission(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, session, _ = self.build(root, [robots(body=b'{"error":"no access"}', headers={"Content-Type": "application/json"})])
            result = crawler.run([ORIGIN + "/manga/12/A"])
            self.assertEqual(len(session.calls), 1)
            self.assertEqual(result["pages_this_run"], 0)
            self.assertIn("invalid_robots_content_type", result["reason"])

    def test_redirect_to_same_series_does_not_overwrite_saved_document(self):
        with tempfile.TemporaryDirectory() as root:
            crawler, session, _ = self.build(root, [robots(), lambda u: response(u),
                lambda u: response(u, 302, headers={"Location": "/manga/12/Other-name"})])
            result = crawler.run([ORIGIN + "/manga/12/A", ORIGIN + "/manga/13/B"])
            self.assertEqual(len(session.calls), 3)
            self.assertEqual(result["pages_this_run"], 1)
            self.assertEqual(statistics(root)["unique_pages"], 1)
            processed = {row["key"]: row for row in Storage(root).load()[1]}
            self.assertEqual(processed["manga:12"]["state"], "success")
            self.assertEqual(processed["manga:13"]["state"], "duplicate")

    def test_request_rate_history_is_preserved_across_resume(self):
        with tempfile.TemporaryDirectory() as root:
            rules = b"User-agent: *\nRequest-rate: 2/30\nAllow: /"
            def slow_robots(url):
                clock.now += 10
                return robots(body=rules)
            crawler, _, clock = self.build(root, [slow_robots, lambda u: response(u)], max_requests=2, max_pages=1)
            crawler.run([ORIGIN + "/manga/12/A", ORIGIN + "/manga/13/B"])
            crawler, session, _ = self.build(root, [robots(body=rules), lambda u: response(u)], max_pages=1)
            crawler.run(resume=True)
            self.assertGreater(session.calls[1][1] - session.calls[0][1], 12)


if __name__ == "__main__":
    unittest.main()
