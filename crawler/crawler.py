"""Sequential MangaBaka crawler."""

import logging
import math
import re
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import quote, unquote

import requests
from requests.adapters import HTTPAdapter

from .robots import RateLimiter, RobotsPolicy, retry_after
from .storage import Storage, atomic_bytes, read_tsv, write_tsv
from .sitemaps import declared_sitemaps, parse_sitemap, sitemap_url
from .validation import blocked_document
from .urls import ORIGIN, CrawlURL, discover_urls, extract_hrefs, normalize_url, safe_absolute

DEFAULT_SEEDS = [
    ORIGIN + "/popular",
    ORIGIN + "/search",
    ORIGIN + "/manga/677/HUNTER-HUNTER",
    ORIGIN + "/manga/676/Ichi-the-Witch",
    ORIGIN + "/manga/849/The-Ancient-Magus-Bride",
]


def utc_now():
    return datetime.now(timezone.utc)


def utc_text(value=None):
    return (value or utc_now()).isoformat()


def seconds_until(value):
    if not value:
        return 0.0
    return max(0.0, (datetime.fromisoformat(value) - utc_now()).total_seconds())


def content_rating_cookie(value):
    """Validate explicit website preferences and encode commas exactly once."""
    if not value:
        return ""
    ratings = list(dict.fromkeys(part.strip().lower() for part in unquote(value).split(',')))
    if not set(ratings) <= {'safe', 'suggestive', 'erotica', 'pornographic'}:
        raise ValueError("Content ratings: použite safe,suggestive,erotica,pornographic oddelené čiarkami.")
    return quote(','.join(ratings), safe='')


@dataclass
class Config:
    output_dir: str = "data"
    max_pages: int = 10
    max_requests: int = 40
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    delay_min: float = 3.0
    delay_max: float = 5.0
    user_agent: str = "MangaIRStudentBot/0.1"
    max_redirects: int = 5
    use_sitemaps: bool = False
    details_only: bool = False
    max_sitemap_urls: int = 50000
    max_sitemaps: int = 2
    content_ratings: str = ""
    retry_content_filtered: bool = False

    def validate(self):
        content_rating_cookie(self.content_ratings)
        if self.retry_content_filtered and not self.content_ratings:
            raise ValueError("--retry-content-filtered vyžaduje --content-ratings.")
        if self.max_pages < 1 or self.max_requests < 1 or self.max_redirects < 0 or self.max_sitemap_urls < 1 or self.max_sitemaps < 0:
            raise ValueError("Limity stránok/požiadaviek musia byť kladné; presmerovania nezáporné.")
        numbers = (self.connect_timeout, self.read_timeout, self.delay_min, self.delay_max)
        if not all(math.isfinite(value) and value > 0 for value in numbers):
            raise ValueError("Timeouty a odstupy musia byť konečné kladné čísla.")
        if self.delay_min < 3 or self.delay_max < self.delay_min:
            raise ValueError("Minimálny odstup je 3 sekundy; maximum nesmie byť menšie.")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*(?:/[^\s]+)?(?: [^\r\n]+)?", self.user_agent):
            raise ValueError("Neplatný User-Agent (použite názov bota, prípadne kontakt).")


@dataclass
class Outcome:
    state: str
    error: str = ""
    target: CrawlURL | None = None
    html_path: str = ""
    saved_bytes: int = 0
    sha256: str = ""
    text: str = ""
    pause: bool = False
    links: tuple = ()


class RequestLimit(Exception):
    pass


def challenge_reason(response):
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return "challenge_page"
    return blocked_document(response.content)


def validate_document(response, target):
    challenge = challenge_reason(response)
    if challenge:
        return challenge
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"text/html", "application/xhtml+xml"}:
        return "not_html"
    # Validate decoded text; save the original response bytes.
    text = response.content.decode(response.encoding or "utf-8", errors="replace")
    title = re.search(r"<title\b[^>]*>(.*?)</title\s*>", text, re.I | re.S)
    heading = re.search(r"<h1\b[^>]*>(.*?)</h1\s*>", text, re.I | re.S)
    if not title or "mangabaka" not in title[1].lower() or not heading:
        return "unrecognized_document"
    heading_text = unescape(re.sub(r"<[^>]*>", "", heading[1])).strip()
    if not heading_text or re.search(r"^(?:404|403|500|error|not found|access denied|sign in)\b", heading_text, re.I):
        return "error_document"
    # When present, the canonical URL must agree with the detail series ID.
    for tag in re.findall(r"<link\b[^>]*>", text, re.I):
        if re.search(r"\brel\s*=\s*['\"]canonical['\"]", tag, re.I):
            hrefs = list(extract_hrefs(tag))
            canonical = normalize_url(hrefs[0], target.url) if hrefs else None
            if target.manga_id and (canonical is None or canonical.manga_id != target.manga_id):
                return "canonical_mismatch"
    return ""


class Crawler:
    def __init__(self, config, *, session=None, limiter=None):
        config.validate()
        self.config = config
        self.storage = Storage(config.output_dir)
        self.session = session if session is not None else requests.Session()
        self.session.headers.update({"User-Agent": config.user_agent,
                                     "Accept": "text/html,application/xhtml+xml;q=0.9",
                                     "Accept-Language": "en"})
        self.content_rating = content_rating_cookie(config.content_ratings)
        if self.content_rating:
            self.session.cookies.set('content_rating', self.content_rating,
                                     domain='mangabaka.org', path='/', secure=True)
        # Handle retries here so each attempt uses the rate limiter.
        self.session.mount("https://", HTTPAdapter(max_retries=0))
        self.limiter = limiter if limiter is not None else RateLimiter(config.delay_min, config.delay_max)
        self.policy = None
        self.pending = []
        self.processed = {}
        self.control = {"forbidden_count": "0", "rate_limited_count": "0", "cooldown_until_utc": ""}
        self.attempt_numbers = Counter(row["requested_url"] for row in read_tsv(self.storage.root / "crawl_metadata.tsv"))
        self.requests_this_run = 0
        self.pages_this_run = 0
        self.sitemaps_this_run = 0
        self.pending_keys = set()
        self.discoveries = []
        self.log = logging.getLogger("crawler")

    def checkpoint(self):
        if self.discoveries:
            import csv
            import os
            path = self.storage.root / 'discovery.tsv'
            new = not path.exists()
            with path.open('a', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=['key', 'url', 'method', 'source_url', 'discovered_at_utc'], delimiter='\t')
                if new:
                    writer.writeheader()
                writer.writerows(self.discoveries)
                stream.flush()
                os.fsync(stream.fileno())
            self.discoveries.clear()
        self.storage.checkpoint(self.pending, list(self.processed.values()), self.control)

    def enqueue(self, target, source_url='', method='html', priority=False):
        if target.key in self.processed or target.key in self.pending_keys:
            return False
        record = {"key": target.key, "url": target.url, "state": "pending",
                  "transient_failures": "0", "not_before_utc": "", "error": ""}
        self.pending.insert(0, record) if priority else self.pending.append(record)
        self.pending_keys.add(target.key)
        self.discoveries.append(dict(key=target.key, url=target.url, method=method,
                                     source_url=source_url, discovered_at_utc=utc_text()))
        return True

    def prepare(self, seeds, resume):
        if self.config.retry_content_filtered and not resume:
            raise ValueError("--retry-content-filtered vyžaduje --resume.")
        targets = []
        for raw in seeds:
            target = normalize_url(raw)
            if target is None:
                raise ValueError(f"Seed URL je mimo povoleného rozsahu: {raw}")
            targets.append(target)
        if resume:
            if not (self.storage.root / "checkpoint.tsv").exists() and not (self.storage.root / "pending_urls.tsv").exists():
                raise ValueError("Nie je k dispozícii stav na obnovenie.")
            self.pending, processed, saved_control = self.storage.load()
            self.pending_keys = {row['key'] for row in self.pending}
            self.processed = {row["key"]: row for row in processed}
            self.control.update(saved_control)
            # Convert saved UTC request times back to the monotonic clock.
            rate_count = int(self.control.get("request_rate_count", "0"))
            rate_window = float(self.control.get("request_rate_window", "0"))
            old_policy = RobotsPolicy(crawl_delay=float(self.control.get("crawl_delay", "0")),
                                      request_rate=(rate_count, rate_window) if rate_count and rate_window else None)
            self.limiter.configure(old_policy)
            clock_now, wall_now = self.limiter.clock(), utc_now()
            for start in self.control.get("request_starts_utc", "").splitlines():
                self.limiter.starts.append(clock_now - (wall_now - datetime.fromisoformat(start)).total_seconds())
            for row in self.pending:
                # Reset exhausted retries; retain partially used retry budgets.
                if int(row["transient_failures"]) >= 3:
                    row["transient_failures"] = "0"
            self.control["robots_failures"] = "0"
            # Recover responses saved before the last checkpoint.
            self.recover_saved_attempts()
        elif any((self.storage.root / name).exists() for name in
                 ("crawl_metadata.tsv", "checkpoint.tsv", "pending_urls.tsv", "processed_urls.tsv")):
            raise ValueError("Adresár obsahuje zber; použite --resume alebo nový --output-dir.")
        self.control['content_rating'] = self.content_rating
        if self.config.retry_content_filtered:
            self.requeue_content_filtered()
        for target in targets:
            self.enqueue(target, method='seed')
        self.control["stop_reason"] = ""
        self.checkpoint()

    def requeue_content_filtered(self):
        targets = []
        for key, row in list(self.processed.items()):
            if (key.startswith('manga:') and row['state'] == 'permanent_failure'
                    and row['error'] == 'content_filtered'):
                target = normalize_url(row['final_url'] or row['url'])
                if target and target.key == key:
                    del self.processed[key]
                    targets.append(target)
        # Put filtered titles at the front of the queue.
        for target in reversed(targets):
            if self.enqueue(target, method='content-preference-retry', priority=True):
                self.pending[0]['error'] = 'content_filtered'
        self.log.info('Vrátené filtrované tituly do fronty: %d', len(targets))

    def recover_saved_attempts(self):
        import hashlib
        latest = {}
        for record in read_tsv(self.storage.root / "crawl_metadata.tsv"):
            if record["error"] or not record["html_path"]:
                continue
            target = normalize_url(record["final_url"])
            if not target:
                continue
            latest[target.key] = (target, record)
        retrying = {row['key'] for row in self.pending if row['error'] == 'content_filtered'}
        for target, record in latest.values():
            path = self.storage.root / record["html_path"]
            if not path.is_file():
                continue
            content = path.read_bytes()
            if len(content) != int(record["saved_bytes"]) or hashlib.sha256(content).hexdigest() != record["sha256"]:
                continue
            if reason := blocked_document(content):
                if target.key in retrying:
                    continue  # A queued retry must survive restart before its request.
                self.complete(normalize_url(record['requested_url']) or target,
                              Outcome('permanent_failure', reason, target=target))
                continue
            original = normalize_url(record["requested_url"])
            if target.key not in self.processed:
                self.complete(original or target, Outcome("success", target=target,
                    html_path=record["html_path"], saved_bytes=len(content), sha256=record["sha256"]))
            for discovered in discover_urls(content.decode("utf-8", errors="replace"), target.url):
                self.enqueue(discovered, source_url=target.url, method='html-recovery')
        for record in read_tsv(self.storage.root / 'sitemap_metadata.tsv'):
            path = self.storage.root / record['path']
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
                continue
            target = sitemap_url(record['url'])
            if not target:
                continue
            document = parse_sitemap(path.read_bytes(), target.url, self.config.max_sitemap_urls)
            if target.key not in self.processed:
                self.complete(target, Outcome('sitemap_success', target=target))
            for discovered in document.links:
                self.enqueue(discovered, source_url=target.url, method='sitemap-recovery')

    @contextmanager
    def attempt(self, url):
        if self.requests_this_run >= self.config.max_requests:
            raise RequestLimit()
        self.limiter.wait()
        self.requests_this_run += 1
        self.attempt_numbers[url] += 1
        target = normalize_url(url)
        row = {"requested_url": url, "final_url": url,
               "manga_id": target.manga_id if target else "", "fetched_at_utc": utc_text(),
               "attempt": self.attempt_numbers[url], "saved_bytes": 0}
        response = None
        self.limiter.started()
        try:
            try:
                response = self.session.get(url, timeout=(self.config.connect_timeout, self.config.read_timeout),
                                            allow_redirects=False)
                row.update(http_status=response.status_code, final_url=response.url,
                           content_type=response.headers.get("Content-Type", ""))
            except requests.RequestException as error:
                row["error"] = type(error).__name__ + ": " + str(error)
            except KeyboardInterrupt:
                row["error"] = "interrupted_request"
                raise
            finally:
                self.limiter.finished()
                self.remember_pacing()
            try:
                yield response, row
            except KeyboardInterrupt:
                row.setdefault("error", "interrupted_processing")
                raise
            except Exception as error:
                row["error"] = "processing_error: " + str(error)
                raise
        finally:
            self.storage.append_attempt(row)
            self.log.info("GET %s status=%s bytes=%s error=%s", url, row.get("http_status", ""),
                          row.get("saved_bytes", 0), row.get("error", ""))
            if response is not None:
                response.close()

    def remember_pacing(self):
        remaining = max(0.0, self.limiter.next_deadline() - self.limiter.clock())
        wall_now, clock_now = utc_now(), self.limiter.clock()
        deadline = utc_text(wall_now + timedelta(seconds=remaining))
        self.control["cooldown_until_utc"] = max(self.control["cooldown_until_utc"], deadline)
        self.control["crawl_delay"] = str(self.limiter.policy.crawl_delay)
        rate = self.limiter.policy.request_rate
        self.control["request_rate_count"] = str(rate[0] if rate else 0)
        self.control["request_rate_window"] = str(rate[1] if rate else 0)
        self.control["request_starts_utc"] = "\n".join(
            utc_text(wall_now + timedelta(seconds=start - clock_now)) for start in self.limiter.starts)

    def defer(self, seconds, pending=None):
        self.limiter.defer(seconds)
        self.remember_pacing()
        if pending is not None:
            pending["not_before_utc"] = self.control["cooldown_until_utc"]

    def fetch(self, original, pending=None, *, robots=False, sitemap=False):
        url = original.url
        redirects = 0
        visited_redirects = {url}
        if pending:
            self.limiter.defer(seconds_until(pending["not_before_utc"]))
        while True:
            target = (sitemap_url(url) if sitemap else normalize_url(url)) if not robots else None
            if not robots and (target is None or not self.policy.allowed(url)):
                return Outcome("permanent_failure", "robots_disallowed" if target else "outside_scope")
            if not robots and target.key != original.key and target.key in self.processed:
                return Outcome("duplicate", "redirect_to_processed", target=target)
            retry = False
            outcome = None
            with self.attempt(url) as (response, row):
                status = response.status_code if response is not None else None
                if response is None or 500 <= status <= 599:
                    error = row.get("error", f"http_{status}")
                    row["error"] = error
                    failures = int(self.control.get("robots_failures", "0") if robots else pending["transient_failures"]) + 1
                    if robots:
                        self.control["robots_failures"] = str(failures)
                    else:
                        pending.update(state="temporary_failure", transient_failures=str(failures), error=error)
                    if failures <= 2:
                        self.defer((10, 30)[failures - 1], pending)
                        retry = True
                    else:
                        outcome = Outcome("temporary_failure", error, pause=robots)
                elif status == 429:
                    count = int(self.control["rate_limited_count"]) + 1
                    self.control["rate_limited_count"] = str(count)
                    error = "http_429"
                    row["error"] = error
                    self.defer(retry_after(response.headers.get("Retry-After")), pending)
                    if pending:
                        pending.update(state="temporary_failure", error=error)
                    if count >= 2:
                        outcome = Outcome("temporary_failure", error, pause=True)
                    else:
                        retry = True
                elif status == 403:
                    count = int(self.control["forbidden_count"]) + 1
                    self.control["forbidden_count"] = str(count)
                    row["error"] = "http_403"
                    outcome = Outcome("temporary_failure" if robots else "permanent_failure", "http_403",
                                      pause=robots or count >= 2)
                elif robots and status in (404, 410):
                    row["error"] = "robots_missing"
                    self.policy = RobotsPolicy()
                    outcome = Outcome("robots_ready")
                elif status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location", "")
                    next_url = safe_absolute(location, url)
                    next_target = (sitemap_url(next_url) if sitemap else normalize_url(next_url)) if next_url else None
                    if robots:
                        valid = next_url is not None and next_url.split("?", 1)[0] == ORIGIN + "/robots.txt"
                    else:
                        valid = next_target is not None and self.policy.allowed(next_target.url)
                        next_url = next_target.url if next_target else None
                    if not valid or redirects >= self.config.max_redirects or next_url in visited_redirects:
                        row["error"] = "unsafe_redirect" if not valid else "redirect_limit_or_loop"
                        outcome = Outcome("permanent_failure", row["error"], pause=robots)
                    else:
                        redirects += 1
                        visited_redirects.add(next_url)
                        url = next_url
                        retry = True
                elif status != 200:
                    row["error"] = f"http_{status}"
                    outcome = Outcome("temporary_failure" if robots else "permanent_failure", row["error"], pause=robots)
                elif robots:
                    error = challenge_reason(response)
                    if not error and ("html" in response.headers.get("Content-Type", "").lower()
                                      or re.search(br"<(?:!doctype|html)\b", response.content[:1000], re.I)):
                        error = "invalid_robots_html"
                    if not error:
                        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                        if content_type not in {"", "text/plain", "application/octet-stream"}:
                            error = "invalid_robots_content_type"
                    if error:
                        row["error"] = error
                        outcome = Outcome("temporary_failure", error, pause=True)
                    else:
                        atomic_bytes(self.storage.root / "robots.txt", response.content)
                        self.policy = RobotsPolicy.parse(response.content.decode("utf-8-sig", errors="replace"), self.config.user_agent)
                        outcome = Outcome("robots_ready")
                elif sitemap:
                    try:
                        if challenge_reason(response):
                            raise ValueError('challenge_page')
                        document = parse_sitemap(response.content, target.url, self.config.max_sitemap_urls)
                        import hashlib
                        digest = hashlib.sha256(response.content).hexdigest()
                        path = 'sitemaps/' + hashlib.sha256(target.url.encode()).hexdigest() + ('.xml.gz' if response.content.startswith(b'\x1f\x8b') else '.xml')
                        atomic_bytes(self.storage.root / path, response.content)
                        records = [r for r in read_tsv(self.storage.root / 'sitemap_metadata.tsv') if r['url'] != target.url]
                        records.append(dict(url=target.url, fetched_at_utc=row['fetched_at_utc'], path=path,
                            saved_bytes=len(response.content), sha256=digest, kind=document.kind, entries=document.entries,
                            accepted_urls=len(document.links), truncated=str(document.truncated).lower()))
                        write_tsv(self.storage.root / 'sitemap_metadata.tsv',
                            ['url', 'fetched_at_utc', 'path', 'saved_bytes', 'sha256', 'kind', 'entries', 'accepted_urls', 'truncated'], records)
                        outcome = Outcome('sitemap_success', target=target, links=document.links)
                    except ValueError as error:
                        row['error'] = str(error)
                        outcome = Outcome('temporary_failure' if str(error) == 'challenge_page' else 'permanent_failure',
                                          str(error), pause=str(error) == 'challenge_page')
                else:
                    error = validate_document(response, target)
                    if error:
                        row["error"] = error
                        outcome = Outcome("temporary_failure" if error == "challenge_page" else "permanent_failure",
                                          error, pause=error == "challenge_page")
                    else:
                        # Save the response before extracting links.
                        path, size, digest = self.storage.save_html(target, response.content)
                        row.update(html_path=path, saved_bytes=size, sha256=digest, manga_id=target.manga_id,
                                   final_url=target.url)
                        outcome = Outcome("success", target=target, html_path=path, saved_bytes=size, sha256=digest,
                                          text=response.content.decode(response.encoding or "utf-8", errors="replace"))
            self.checkpoint()
            if outcome is not None:
                return outcome
            if not retry:
                raise RuntimeError("Chýba výsledok HTTP pokusu.")

    def complete(self, original, outcome):
        final = outcome.target or original
        record = {"key": final.key, "url": original.url, "final_url": final.url,
                  "state": outcome.state, "manga_id": final.manga_id,
                  "html_path": outcome.html_path, "saved_bytes": outcome.saved_bytes,
                  "sha256": outcome.sha256, "error": outcome.error}
        # A duplicate alias must not replace an already successful processed record.
        if outcome.state != "duplicate" or final.key not in self.processed:
            self.processed[final.key] = record
        if original.key != final.key:
            self.processed[original.key] = {**record, "key": original.key, "state": "duplicate",
                                            "html_path": "", "saved_bytes": 0, "sha256": ""}
        self.pending = [row for row in self.pending if row["key"] not in {original.key, final.key}]
        self.pending_keys = {row['key'] for row in self.pending}

    def run(self, seeds=(), *, resume=False):
        reason = "queue_exhausted"
        prepared = False
        with self.storage.lock():
            try:
                self.prepare(seeds, resume)
                prepared = True
                if not self.pending:
                    return {"reason": reason, "pages_this_run": 0, "requests_this_run": 0}
                self.limiter.defer(seconds_until(self.control["cooldown_until_utc"]))
                robots = self.fetch(CrawlURL(ORIGIN + "/robots.txt", "robots"), robots=True)
                if robots.state != "robots_ready":
                    reason = "robots_unavailable: " + robots.error
                else:
                    self.limiter.configure(self.policy)
                    self.remember_pacing()
                    if self.config.use_sitemaps:
                        robots_path = self.storage.root / 'robots.txt'
                        if robots_path.exists():
                            for target in declared_sitemaps(robots_path.read_text(encoding='utf-8-sig')):
                                self.enqueue(target, source_url=ORIGIN + '/robots.txt', method='robots-sitemap', priority=True)
                        self.checkpoint()
                    visited = set()
                    while self.pages_this_run < self.config.max_pages:
                        row = next((p for p in self.pending if p["key"] not in visited and
                                    (not self.config.details_only or p['key'].startswith(('manga:', 'sitemap:'))) and
                                    (not p['key'].startswith('sitemap:') or self.sitemaps_this_run < self.config.max_sitemaps)), None)
                        if row is None:
                            reason = "temporary_failures_remaining" if any(p['state'] == 'temporary_failure' for p in self.pending) else ("scope_exhausted" if self.pending else "queue_exhausted")
                            break
                        is_sitemap = row['key'].startswith('sitemap:')
                        target = sitemap_url(row['url']) if is_sitemap else normalize_url(row["url"])
                        if target is None:
                            raise ValueError("Checkpoint obsahuje URL mimo rozsahu.")
                        outcome = self.fetch(target, row, sitemap=is_sitemap)
                        visited.add(row["key"])
                        if outcome.state == "temporary_failure":
                            row.update(state=outcome.state, error=outcome.error)
                        else:
                            self.complete(target, outcome)
                        if outcome.state == "success":
                            self.pages_this_run += 1
                            for discovered in discover_urls(outcome.text, outcome.target.url):
                                self.enqueue(discovered, source_url=outcome.target.url)
                        elif outcome.state == 'sitemap_success':
                            self.sitemaps_this_run += 1
                            for discovered in outcome.links:
                                if self.policy.allowed(discovered.url):
                                    self.enqueue(discovered, source_url=outcome.target.url, method='sitemap', priority=discovered.key.startswith('sitemap:'))
                        self.checkpoint()
                        if outcome.pause:
                            reason = "paused: " + outcome.error
                            break
                    else:
                        reason = "page_limit"
            except RequestLimit:
                reason = "request_limit"
            except KeyboardInterrupt:
                reason = "interrupted"
            finally:
                # The active URL stays in pending until its successful/terminal commit.
                if prepared:
                    self.control["stop_reason"] = reason
                    self.checkpoint()
                self.session.close()
            # Apply the trailing post-request pause, even at a page/request limit.
            try:
                if (self.limiter.last_finish is not None and not reason.startswith(("paused", "robots_unavailable"))
                        and reason != "interrupted"):
                    self.limiter.wait()
            except KeyboardInterrupt:
                reason = "interrupted"
                self.control["stop_reason"] = reason
                self.checkpoint()
        return {"reason": reason, "pages_this_run": self.pages_this_run,
                "requests_this_run": self.requests_this_run}
