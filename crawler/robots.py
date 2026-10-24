"""Robots rules and monotonic request pacing, with no implicit network I/O."""

import math
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit


def retry_after(value: str | None, now: datetime | None = None) -> float:
    """Invalid/missing headers conservatively mean at least 60 seconds."""
    if value:
        value = value.strip()
        if re.fullmatch(r"[0-9]+", value):
            return float(value)
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            return max(0.0, (deadline - (now or datetime.now(timezone.utc))).total_seconds())
        except (TypeError, ValueError, OverflowError):
            pass
    return 60.0


def _robots_octets(value):
    # RFC 9309: decode percent-encoded unreserved ASCII, preserve reserved octets.
    value = quote(value, safe="/%?=&:;+$,@!*'()-._~")
    def decode(match):
        char = chr(int(match.group(1), 16))
        return char if char.isascii() and (char.isalnum() or char in "-._~") else match.group().upper()
    return re.sub(r"%([0-9a-fA-F]{2})", decode, value)


@dataclass
class RobotsPolicy:
    rules: list[tuple[bool, str]] = field(default_factory=list)
    crawl_delay: float = 0.0
    request_rate: tuple[int, float] | None = None

    @classmethod
    def parse(cls, text, user_agent):
        groups = []
        agents, entries = [], []
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            name, value = (p.strip() for p in line.split(":", 1))
            name = name.lower()
            if name == "user-agent":
                if entries:
                    groups.append((agents, entries))
                    agents, entries = [], []
                agents.append(value.lower())
            elif agents:
                entries.append((name, value))
        if agents:
            groups.append((agents, entries))
        product = user_agent.split("/", 1)[0].split()[0].lower()
        scored = []
        for agents, entries in groups:
            scores = [0 if a == "*" else len(a) for a in agents if a == "*" or a in product]
            if scores:
                scored.append((max(scores), entries))
        best = max((score for score, _ in scored), default=-1)
        result = cls()
        for score, entries in scored:
            if score != best:
                continue
            for name, value in entries:
                if name in {"allow", "disallow"} and value:
                    result.rules.append((name == "allow", _robots_octets(value)))
                elif name == "crawl-delay":
                    try:
                        delay = float(value)
                        if math.isfinite(delay) and delay >= 0:
                            result.crawl_delay = max(result.crawl_delay, delay)
                    except ValueError:
                        pass
                elif name == "request-rate":
                    match = re.fullmatch(r"([0-9]+)/([0-9]+(?:\.[0-9]+)?)", value)
                    if match:
                        rate = (int(match[1]), float(match[2]))
                        if rate[0] > 0 and rate[1] > 0 and math.isfinite(rate[1]):
                            # Conservative merging of duplicate matching groups.
                            if result.request_rate is None:
                                result.request_rate = rate
                            else:
                                result.request_rate = (min(rate[0], result.request_rate[0]),
                                                       max(rate[1], result.request_rate[1]))
        return result

    def allowed(self, url):
        parts = urlsplit(url)
        path = _robots_octets(parts.path + ("?" + parts.query if parts.query else ""))
        matches = []
        for allow, pattern in self.rules:
            anchored = pattern.endswith("$")
            body = pattern[:-1] if anchored else pattern
            expression = "^" + ".*".join(re.escape(p) for p in body.split("*"))
            if re.search(expression + ("$" if anchored else ""), path):
                octets = re.sub(r"%[0-9A-F]{2}", "x", body.replace("*", ""))
                matches.append((len(octets), allow))
        return max(matches, default=(0, True))[1]


class RateLimiter:
    def __init__(self, delay_min=3.0, delay_max=5.0, *, clock=time.monotonic,
                 sleep=time.sleep, uniform=random.uniform):
        self.delay_min, self.delay_max = delay_min, delay_max
        self.clock, self.sleep, self.uniform = clock, sleep, uniform
        self.policy = RobotsPolicy()
        self.deadline = clock()
        self.last_finish = None
        self.starts = deque()

    def configure(self, policy):
        self.policy = policy
        if self.last_finish is not None:
            self.deadline = max(self.deadline, self.last_finish + policy.crawl_delay)

    def defer(self, seconds):
        self.deadline = max(self.deadline, self.clock() + seconds)

    def wait(self):
        while True:
            remaining = self.next_deadline() - self.clock()
            if remaining <= 0:
                return
            # Short sleeps keep signal handling responsive even for long Retry-After.
            self.sleep(min(remaining, 1.0))

    def next_deadline(self):
        deadline = self.deadline
        if self.policy.request_rate:
            count, window = self.policy.request_rate
            while self.starts and self.starts[0] <= self.clock() - window:
                self.starts.popleft()
            if len(self.starts) >= count:
                deadline = max(deadline, self.starts[-count] + window)
        return deadline

    def started(self):
        if self.policy.request_rate is None:
            self.starts.clear()
        self.starts.append(self.clock())

    def finished(self):
        self.last_finish = self.clock()
        delay = max(self.uniform(self.delay_min, self.delay_max), self.policy.crawl_delay)
        self.deadline = max(self.deadline, self.last_finish + delay)
