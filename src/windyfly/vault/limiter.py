"""Self-limits for provider calls (strand gene G6.3; Vault plan section 8, GitHub rate limits).

Under leases the Vault cannot count the agent's provider calls, and one runaway agent can
exhaust an owner's installation limit or draw secondary limits on the shared App. This is a
harness-side token bucket, a Retry-After honorer and a circuit breaker. It protects an honest
agent from itself; it is NOT enforceable against a rogue agent (said plainly in the plan).
"""

from __future__ import annotations

import email.utils
import threading
import time
from collections.abc import Callable


class Limited(Exception):
    def __init__(self, wait_s: float, reason: str) -> None:
        super().__init__(reason)
        self.wait_s, self.reason = wait_s, reason


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    if not value:
        return None
    v = value.strip()
    if v.isdigit():
        return float(v)
    try:
        dt = email.utils.parsedate_to_datetime(v)
        return max(0.0, dt.timestamp() - (now if now is not None else time.time()))
    except (TypeError, ValueError):
        return None


class ProviderLimiter:
    """One per (provider, connection). ``acquire`` raises Limited instead of sleeping."""

    def __init__(self, rate_per_s: float = 1.0, burst: int = 10, *, fail_threshold: int = 5,
                 open_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._rate, self._burst = rate_per_s, float(burst)
        self._tokens, self._at = float(burst), clock()
        self._clock = clock
        self._blocked_until = 0.0
        self._fails = 0
        self._fail_threshold, self._open_s = fail_threshold, open_s
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._blocked_until:
                raise Limited(self._blocked_until - now, "blocked")
            self._tokens = min(self._burst, self._tokens + (now - self._at) * self._rate)
            self._at = now
            if self._tokens < 1.0:
                raise Limited((1.0 - self._tokens) / self._rate, "rate")
            self._tokens -= 1.0

    def on_response(self, status: int, headers: dict[str, str] | None = None) -> None:
        """Feed every answer back: 429/403+Retry-After block for that long; 5xx streaks open the breaker."""
        h = {k.lower(): v for k, v in (headers or {}).items()}
        with self._lock:
            now = self._clock()
            ra = parse_retry_after(h.get("retry-after"))
            if status in (429, 403) and (ra is not None or h.get("x-ratelimit-remaining") == "0"):
                reset = h.get("x-ratelimit-reset")
                wait = ra if ra is not None else (max(0.0, float(reset) - time.time()) if reset and reset.isdigit() else 60.0)
                self._blocked_until = max(self._blocked_until, now + min(wait, 3600.0))
            if status >= 500 or status == 429:
                self._fails += 1
                if self._fails >= self._fail_threshold:
                    self._blocked_until = max(self._blocked_until, now + self._open_s)
                    self._fails = 0
            elif status < 400:
                self._fails = 0
