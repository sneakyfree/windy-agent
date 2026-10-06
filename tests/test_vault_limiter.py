"""Strand G6.3: harness-side limits for provider calls."""

from __future__ import annotations

import pytest

from windyfly.vault.limiter import Limited, ProviderLimiter, parse_retry_after


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_token_bucket_paces_a_burst_and_refills():
    c = Clock()
    lim = ProviderLimiter(rate_per_s=2.0, burst=3, clock=c)
    for _ in range(3):
        lim.acquire()
    with pytest.raises(Limited) as e:
        lim.acquire()
    assert e.value.reason == "rate" and 0 < e.value.wait_s <= 0.5
    c.t += 0.5
    lim.acquire()


def test_retry_after_blocks_for_that_long():
    c = Clock()
    lim = ProviderLimiter(clock=c)
    lim.on_response(429, {"Retry-After": "30"})
    with pytest.raises(Limited) as e:
        lim.acquire()
    assert e.value.reason == "blocked" and 29 < e.value.wait_s <= 30
    c.t += 31
    lim.acquire()


def test_secondary_limit_403_with_retry_after_also_blocks():
    c = Clock()
    lim = ProviderLimiter(clock=c)
    lim.on_response(403, {"retry-after": "10"})
    with pytest.raises(Limited):
        lim.acquire()


def test_a_plain_403_does_not_block():
    lim = ProviderLimiter()
    lim.on_response(403, {})
    lim.acquire()


def test_breaker_opens_after_a_streak_of_failures_and_recovers():
    c = Clock()
    lim = ProviderLimiter(fail_threshold=3, open_s=60, clock=c)
    for _ in range(3):
        lim.on_response(502)
    with pytest.raises(Limited):
        lim.acquire()
    c.t += 61
    lim.acquire()
    lim.on_response(200)


def test_retry_after_parsing():
    assert parse_retry_after("12") == 12.0 and parse_retry_after(None) is None and parse_retry_after("junk") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=0) > 0
