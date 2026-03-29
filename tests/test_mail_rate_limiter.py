"""Tests for mail rate limiter."""

from __future__ import annotations

import pytest

from windyfly.mail_rate_limiter import MailRateLimiter, RateLimitResult
from windyfly.memory.database import Database


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture
def limiter(db):
    return MailRateLimiter(db, limits={
        "max_per_hour": 5,
        "max_per_day": 10,
        "max_unique_recipients_per_day": 3,
        "max_per_minute": 1000,  # High enough to not interfere with other tests
    })


@pytest.fixture
def daily_limiter(db):
    """Limiter with high hourly limit but low daily limit."""
    return MailRateLimiter(db, limits={
        "max_per_hour": 1000,
        "max_per_day": 5,
        "max_unique_recipients_per_day": 100,
        "max_per_minute": 1000,
    })


class TestRateLimiting:
    def test_allows_first_send(self, limiter):
        result = limiter.check_send_allowed("fly@windymail.ai", "user@example.com")
        assert result.allowed is True

    def test_daily_limit(self, daily_limiter):
        """Should block after max_per_day sends."""
        for i in range(5):
            daily_limiter.record_send("fly@windymail.ai", f"user{i}@example.com")

        result = daily_limiter.check_send_allowed("fly@windymail.ai", "new@example.com")
        assert result.allowed is False
        assert "Daily limit" in result.reason

    def test_hourly_limit(self, limiter):
        """Should block after max_per_hour sends."""
        for i in range(5):
            limiter.record_send("fly@windymail.ai", "same@example.com")

        result = limiter.check_send_allowed("fly@windymail.ai", "same@example.com")
        assert result.allowed is False
        assert "Hourly limit" in result.reason

    def test_recipient_diversity(self, limiter):
        """Should block new recipients after max_unique_recipients_per_day."""
        for i in range(3):
            limiter.record_send("fly@windymail.ai", f"user{i}@example.com")

        # Existing recipient should still be allowed
        result = limiter.check_send_allowed("fly@windymail.ai", "user0@example.com")
        assert result.allowed is True

        # New recipient should be blocked
        result = limiter.check_send_allowed("fly@windymail.ai", "brand-new@example.com")
        assert result.allowed is False
        assert "Recipient diversity" in result.reason


class TestContentReputation:
    def test_excessive_caps(self, limiter):
        result = limiter.check_send_allowed(
            "fly@windymail.ai", "user@example.com",
            subject="BUY NOW AMAZING DEAL FREE FREE FREE",
            body="THIS IS ALL CAPS AND VERY SPAMMY LOOKING TEXT",
        )
        assert result.allowed is False
        assert "capitals" in result.reason.lower()

    def test_normal_text_allowed(self, limiter):
        result = limiter.check_send_allowed(
            "fly@windymail.ai", "user@example.com",
            subject="Meeting notes",
            body="Here are the notes from today's meeting.",
        )
        assert result.allowed is True

    def test_excessive_links(self, limiter):
        body = " ".join(f"https://link{i}.com" for i in range(10))
        result = limiter.check_send_allowed(
            "fly@windymail.ai", "user@example.com",
            subject="Links", body=body,
        )
        assert result.allowed is False
        assert "links" in result.reason.lower()


class TestVelocity:
    def test_per_minute_limit(self, db):
        lim = MailRateLimiter(db, limits={
            "max_per_hour": 100,
            "max_per_day": 100,
            "max_unique_recipients_per_day": 100,
            "max_per_minute": 2,
        })
        lim.record_send("fly@windymail.ai", "a@example.com")
        lim.record_send("fly@windymail.ai", "b@example.com")
        result = lim.check_send_allowed("fly@windymail.ai", "c@example.com")
        assert result.allowed is False
        assert "Velocity" in result.reason


class TestRecordSend:
    def test_record_increments_count(self, limiter):
        limiter.record_send("fly@windymail.ai", "user@example.com", "body")
        result = limiter.check_send_allowed("fly@windymail.ai", "user@example.com")
        assert result.sends_today == 1
