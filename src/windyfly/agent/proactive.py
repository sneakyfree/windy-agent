"""Proactive check-in — a great assistant reaches out, not just responds.

Features:
- Daily check-in with to-do summary (if auto_checkin = true)
- Reminder follow-up if user doesn't respond within 30 minutes
- Only one follow-up per reminder (no spam)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from windyfly.memory.database import Database

logger = logging.getLogger(__name__)


def should_send_checkin(db: Database, config: dict) -> bool:
    """Check if a daily check-in is due.

    Only triggers if:
    - auto_checkin is enabled in config
    - User hasn't chatted in 24 hours
    - We haven't already sent a check-in today
    """
    if not config.get("personality", {}).get("auto_checkin", False):
        return False

    # Check last check-in time
    last_checkin = db.fetchone(
        "SELECT value FROM soul WHERE key = 'last_checkin_date'"
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if last_checkin and last_checkin["value"] == today:
        return False

    # Check last user message time
    last_msg = db.fetchone(
        "SELECT created_at FROM episodes WHERE role = 'user' ORDER BY created_at DESC LIMIT 1"
    )
    if last_msg:
        try:
            last_time = datetime.fromisoformat(last_msg["created_at"])
            if (datetime.now(timezone.utc) - last_time).total_seconds() < 86400:
                return False  # User chatted within 24h
        except (ValueError, TypeError):
            pass

    return True


def generate_checkin_message(db: Database) -> str:
    """Generate a daily check-in message with context."""
    parts = ["Good morning! 🪰"]

    # Check to-do items
    try:
        todos = db.fetchall(
            "SELECT title FROM todos WHERE completed = FALSE ORDER BY created_at LIMIT 5"
        )
        if todos:
            count = len(todos)
            parts.append(f"You have {count} item{'s' if count != 1 else ''} on your to-do list.")
            if count <= 3:
                for t in todos:
                    parts.append(f"  • {t['title']}")
            parts.append("Want to review them?")
    except Exception:
        pass

    # Check upcoming reminders
    try:
        now = datetime.now(timezone.utc)
        end_of_day = now.replace(hour=23, minute=59, second=59)
        reminders = db.fetchall(
            "SELECT message, remind_at FROM reminders "
            "WHERE delivered = FALSE AND remind_at <= ? ORDER BY remind_at LIMIT 3",
            (end_of_day.isoformat(),),
        )
        if reminders:
            parts.append(f"You also have {len(reminders)} reminder{'s' if len(reminders) != 1 else ''} today.")
    except Exception:
        pass

    if len(parts) == 1:
        parts.append("No tasks or reminders for today. Enjoy your day!")

    return " ".join(parts)


def mark_checkin_sent(db: Database) -> None:
    """Record that today's check-in was sent."""
    from windyfly.memory.soul import upsert_soul
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    upsert_soul(db, key="last_checkin_date", value=today, source="proactive")


def get_unacknowledged_reminders(db: Database, minutes: int = 30) -> list[dict[str, Any]]:
    """Find reminders delivered more than N minutes ago with no user response.

    Only returns reminders that haven't already had a follow-up sent.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

    try:
        # Find delivered reminders older than cutoff
        delivered = db.fetchall(
            "SELECT id, message FROM reminders "
            "WHERE delivered = TRUE AND remind_at <= ? AND repeat IS NULL",
            (cutoff,),
        )

        # Filter out ones we've already followed up on
        result = []
        for r in delivered:
            followup = db.fetchone(
                "SELECT value FROM soul WHERE key = ?",
                (f"reminder_followup_{r['id']}",),
            )
            if not followup:
                result.append(r)

        return result
    except Exception:
        return []


def mark_followup_sent(db: Database, reminder_id: str) -> None:
    """Mark that we sent a follow-up for this reminder."""
    from windyfly.memory.soul import upsert_soul
    upsert_soul(
        db,
        key=f"reminder_followup_{reminder_id}",
        value=datetime.now(timezone.utc).isoformat(),
        source="proactive",
    )


def generate_followup_message(reminder: dict[str, Any]) -> str:
    """Generate a follow-up message for an unacknowledged reminder."""
    return f"⏰ Reminder: {reminder['message']} (from earlier). Still need this?"
