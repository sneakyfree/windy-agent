"""Mail rate limiting engine for Windy Fly agents.

Prevents newly hatched agents from being flagged as spam by enforcing
velocity limits, recipient diversity caps, and content reputation scoring.
"""

from __future__ import annotations

import hashlib
import re
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from windyfly.memory.database import Database

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS mail_rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_addr TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    content_hash TEXT DEFAULT '',
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# Default limits (configurable via windyfly.toml [mail_limits])
DEFAULT_LIMITS = {
    "max_per_hour": 10,
    "max_per_day": 50,
    "max_unique_recipients_per_day": 25,
    "max_per_minute": 3,
}


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    reason: str = ""
    sends_today: int = 0
    sends_this_hour: int = 0
    unique_recipients_today: int = 0


class MailRateLimiter:
    """Rate limiter for outbound agent emails.

    Backed by SQLite for persistence across restarts.
    """

    def __init__(self, db: Database, limits: dict | None = None) -> None:
        self.db = db
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.db.conn.executescript(_CREATE_TABLE_SQL)

    def check_send_allowed(
        self,
        from_addr: str,
        to_addr: str,
        subject: str = "",
        body: str = "",
    ) -> RateLimitResult:
        """Check if an outbound email is allowed under rate limits."""
        # Per-minute velocity
        per_min = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM mail_rate_limits "
            "WHERE from_addr = ? AND sent_at >= datetime('now', '-1 minute')",
            (from_addr,),
        )
        if per_min and per_min["cnt"] >= self.limits["max_per_minute"]:
            return RateLimitResult(
                allowed=False,
                reason=f"Velocity limit: max {self.limits['max_per_minute']}/minute",
                sends_this_hour=self._count_this_hour(from_addr),
                sends_today=self._count_today(from_addr),
            )

        # Per-hour
        per_hour = self._count_this_hour(from_addr)
        if per_hour >= self.limits["max_per_hour"]:
            return RateLimitResult(
                allowed=False,
                reason=f"Hourly limit: max {self.limits['max_per_hour']}/hour",
                sends_this_hour=per_hour,
                sends_today=self._count_today(from_addr),
            )

        # Per-day
        per_day = self._count_today(from_addr)
        if per_day >= self.limits["max_per_day"]:
            return RateLimitResult(
                allowed=False,
                reason=f"Daily limit: max {self.limits['max_per_day']}/day",
                sends_today=per_day,
            )

        # Unique recipients per day
        unique = self._unique_recipients_today(from_addr)
        # Only count as new if this recipient hasn't been seen today
        is_new = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM mail_rate_limits "
            "WHERE from_addr = ? AND to_addr = ? AND sent_at >= date('now', 'start of day')",
            (from_addr, to_addr),
        )
        new_recipient = is_new and is_new["cnt"] == 0
        if new_recipient and unique >= self.limits["max_unique_recipients_per_day"]:
            return RateLimitResult(
                allowed=False,
                reason=f"Recipient diversity: max {self.limits['max_unique_recipients_per_day']} unique/day",
                unique_recipients_today=unique,
                sends_today=per_day,
            )

        # Content reputation check
        content_issue = self._check_content(subject, body)
        if content_issue:
            return RateLimitResult(
                allowed=False,
                reason=f"Content flag: {content_issue}",
                sends_today=per_day,
            )

        return RateLimitResult(
            allowed=True,
            sends_today=per_day,
            sends_this_hour=per_hour,
            unique_recipients_today=unique + (1 if new_recipient else 0),
        )

    def record_send(self, from_addr: str, to_addr: str, body: str = "") -> None:
        """Record that an email was sent (for rate tracking)."""
        content_hash = hashlib.md5(body.encode()).hexdigest() if body else ""
        self.db.execute(
            "INSERT INTO mail_rate_limits (from_addr, to_addr, content_hash) VALUES (?, ?, ?)",
            (from_addr, to_addr, content_hash),
        )
        self.db.commit()

    def _count_this_hour(self, from_addr: str) -> int:
        row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM mail_rate_limits "
            "WHERE from_addr = ? AND sent_at >= datetime('now', '-1 hour')",
            (from_addr,),
        )
        return row["cnt"] if row else 0

    def _count_today(self, from_addr: str) -> int:
        row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM mail_rate_limits "
            "WHERE from_addr = ? AND sent_at >= date('now', 'start of day')",
            (from_addr,),
        )
        return row["cnt"] if row else 0

    def _unique_recipients_today(self, from_addr: str) -> int:
        row = self.db.fetchone(
            "SELECT COUNT(DISTINCT to_addr) as cnt FROM mail_rate_limits "
            "WHERE from_addr = ? AND sent_at >= date('now', 'start of day')",
            (from_addr,),
        )
        return row["cnt"] if row else 0

    def _check_content(self, subject: str, body: str) -> str:
        """Basic content reputation check. Returns issue string or empty."""
        text = f"{subject} {body}"

        # All caps check (more than 50% of alpha chars are uppercase)
        alpha = [c for c in text if c.isalpha()]
        if alpha and sum(1 for c in alpha if c.isupper()) / len(alpha) > 0.5:
            if len(alpha) > 20:  # Only flag substantial text
                return "Excessive capitals"

        # Excessive links
        urls = re.findall(r"https?://", text)
        if len(urls) > 5:
            return "Too many links"

        return ""
