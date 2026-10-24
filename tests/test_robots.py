import unittest
from datetime import datetime, timezone

from crawler.robots import RateLimiter, RobotsPolicy, retry_after


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds

    def limiter(self):
        return RateLimiter(clock=self.clock, sleep=self.sleep, uniform=lambda a, b: a)


class RobotsTests(unittest.TestCase):
    def test_retry_after_seconds_date_and_fallback(self):
        now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
        self.assertEqual(retry_after("120", now), 120)
        self.assertEqual(retry_after("Sun, 11 Oct 2026 12:00:45 GMT", now), 45)
        self.assertEqual(retry_after("Sun, 11 Oct 2026 11:59:00 GMT", now), 0)
        self.assertEqual(retry_after("0", now), 0)
        for value in (None, "", "bad date", "-1", "1.5"):
            self.assertEqual(retry_after(value, now), 60)

    def test_user_agent_specificity_and_rule_priority(self):
        policy = RobotsPolicy.parse('''User-agent: *
Disallow: /
User-agent: MangaIRStudentBot
Disallow: /search
Disallow: /manga/*/blocked$
Allow: /search/allowed
Allow: /
Crawl-delay: 8
Request-rate: 2/30
User-agent: mangairStudentBot
Disallow: /auth
''', "MangaIRStudentBot/0.1 (+https://example.org/project)")
        self.assertTrue(policy.allowed("https://mangabaka.org/manga/12/A"))
        self.assertFalse(policy.allowed("https://mangabaka.org/search?page=2"))
        self.assertTrue(policy.allowed("https://mangabaka.org/search/allowed"))
        self.assertFalse(policy.allowed("https://mangabaka.org/manga/12/blocked"))
        self.assertTrue(policy.allowed("https://mangabaka.org/manga/12/blocked-more"))
        self.assertFalse(policy.allowed("https://mangabaka.org/auth"))
        self.assertEqual(policy.crawl_delay, 8)
        self.assertEqual(policy.request_rate, (2, 30))

    def test_percent_encoding_and_allow_tie(self):
        policy = RobotsPolicy.parse("User-agent: *\nDisallow: /caf%C3%A9\nDisallow: /same\nAllow: /same", "Bot/1")
        self.assertFalse(policy.allowed("https://mangabaka.org/café"))
        self.assertTrue(policy.allowed("https://mangabaka.org/same"))

    def test_monotonic_pacing_and_rate_window(self):
        clock = FakeClock()
        limiter = clock.limiter()
        limiter.configure(RobotsPolicy(crawl_delay=4, request_rate=(2, 30)))
        starts = []
        for _ in range(3):
            limiter.wait()
            starts.append(clock.now)
            limiter.started()
            clock.now += 2  # response took two seconds
            limiter.finished()
        self.assertEqual(starts, [0, 6, 30])
        limiter.defer(60)
        limiter.wait()
        self.assertEqual(clock.now, 92)
        self.assertTrue(clock.waits)


if __name__ == "__main__":
    unittest.main()
