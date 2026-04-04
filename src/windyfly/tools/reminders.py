"""Reminders & timers — "Remind me to take my medicine at 2pm."

Stores reminders in SQLite, runs a background checker every 30s,
and delivers via chat/SMS/email when due.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from windyfly.memory.database import Database
    from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_REMINDERS_SQL = """
CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    message TEXT NOT NULL,
    remind_at DATETIME NOT NULL,
    repeat TEXT,
    channel TEXT DEFAULT 'chat',
    delivered BOOLEAN DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def _ensure_table(db: Database) -> None:
    db.conn.executescript(_REMINDERS_SQL)


def _parse_time(time_str: str) -> datetime | None:
    """Parse natural language time to datetime.

    Supports: "in 20 minutes", "at 3pm", "at 15:00",
    "tomorrow at 9am", "in 2 hours".
    """
    now = datetime.now(timezone.utc)
    s = time_str.strip().lower()

    # Relative: "in X minutes/hours/seconds"
    m = re.match(r"in\s+(\d+)\s*(min(?:ute)?s?|hours?|hrs?|seconds?|secs?|days?)", s)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("min"):
            return now + timedelta(minutes=amount)
        if unit.startswith(("hour", "hr")):
            return now + timedelta(hours=amount)
        if unit.startswith("sec"):
            return now + timedelta(seconds=amount)
        if unit.startswith("day"):
            return now + timedelta(days=amount)

    # Absolute: "at 3pm", "at 3:00 PM", "at 15:00"
    m = re.match(r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    # "tomorrow at Xam/pm"
    m = re.match(r"tomorrow\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        target = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return target

    return None


def set_reminder(
    db: Database,
    message: str,
    time_str: str,
    repeat: str | None = None,
    channel: str = "chat",
    user_id: str = "default",
) -> dict[str, Any]:
    """Set a new reminder."""
    _ensure_table(db)

    remind_at = _parse_time(time_str)
    if remind_at is None:
        return {"success": False, "error": f"Could not parse time: {time_str}"}

    reminder_id = str(uuid.uuid4())[:8]
    db.execute(
        "INSERT INTO reminders (id, user_id, message, remind_at, repeat, channel) VALUES (?, ?, ?, ?, ?, ?)",
        (reminder_id, user_id, message, remind_at.isoformat(), repeat, channel),
    )
    db.commit()

    time_fmt = remind_at.strftime("%I:%M %p").lstrip("0")
    return {
        "success": True,
        "id": reminder_id,
        "message": f"I'll remind you to {message} at {time_fmt}.",
        "remind_at": remind_at.isoformat(),
    }


def list_reminders(db: Database, user_id: str = "default") -> dict[str, Any]:
    """List upcoming reminders."""
    _ensure_table(db)
    rows = db.fetchall(
        "SELECT id, message, remind_at, repeat, channel FROM reminders "
        "WHERE user_id = ? AND delivered = FALSE ORDER BY remind_at",
        (user_id,),
    )
    if not rows:
        return {"reminders": [], "message": "No upcoming reminders."}

    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "message": r["message"],
            "time": r["remind_at"],
            "repeat": r["repeat"],
            "channel": r["channel"],
        })
    return {"reminders": items, "message": f"You have {len(items)} upcoming reminder(s)."}


def cancel_reminder(db: Database, reminder_id: str, user_id: str = "default") -> dict[str, Any]:
    """Cancel a reminder by ID."""
    _ensure_table(db)
    row = db.fetchone(
        "SELECT * FROM reminders WHERE id = ? AND user_id = ?",
        (reminder_id, user_id),
    )
    if not row:
        return {"success": False, "error": f"Reminder {reminder_id} not found."}

    db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    db.commit()
    return {"success": True, "message": f"Cancelled reminder: {row['message']}"}


def get_due_reminders(db: Database) -> list[dict]:
    """Get all reminders that are due now."""
    _ensure_table(db)
    now = datetime.now(timezone.utc).isoformat()
    return db.fetchall(
        "SELECT * FROM reminders WHERE remind_at <= ? AND delivered = FALSE",
        (now,),
    )


def mark_delivered(db: Database, reminder_id: str, repeat: str | None = None) -> None:
    """Mark a reminder as delivered. Schedule next if recurring."""
    if repeat:
        now = datetime.now(timezone.utc)
        if repeat == "daily":
            next_at = now + timedelta(days=1)
        elif repeat == "weekly":
            next_at = now + timedelta(weeks=1)
        elif repeat == "monthly":
            next_at = now + timedelta(days=30)
        else:
            next_at = None

        if next_at:
            db.execute(
                "UPDATE reminders SET remind_at = ?, delivered = FALSE WHERE id = ?",
                (next_at.isoformat(), reminder_id),
            )
            db.commit()
            return

    db.execute("UPDATE reminders SET delivered = TRUE WHERE id = ?", (reminder_id,))
    db.commit()


def start_reminder_checker(db: Database, deliver_fn: Any = None) -> threading.Thread:
    """Start background thread that checks reminders every 30 seconds.

    Args:
        db: Database instance.
        deliver_fn: Callable(reminder_dict) to deliver the reminder.
            If None, just logs.
    """
    def _check_loop() -> None:
        while True:
            try:
                due = get_due_reminders(db)
                for r in due:
                    msg = f"⏰ Reminder: {r['message']}"
                    if deliver_fn:
                        try:
                            deliver_fn(msg, r.get("channel", "chat"))
                        except Exception as e:
                            logger.warning("Reminder delivery failed: %s", e)
                    else:
                        logger.info("REMINDER DUE: %s", msg)
                    mark_delivered(db, r["id"], r.get("repeat"))
            except Exception as e:
                logger.debug("Reminder check error: %s", e)
            time.sleep(30)

    t = threading.Thread(target=_check_loop, daemon=True, name="reminder-checker")
    t.start()
    return t


def register_reminder_tools(registry: ToolRegistry, db: Database) -> None:
    """Register reminder tools with the LLM tool registry."""
    registry.register(
        name="set_reminder",
        description=(
            "Set a reminder for the user. Use when they say 'remind me to...' "
            "or 'set a timer for...'. Parse the time naturally."
        ),
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "What to remind about"},
                "time": {"type": "string", "description": "When: 'in 20 minutes', 'at 3pm', 'tomorrow at 9am'"},
                "repeat": {"type": "string", "description": "Optional: 'daily', 'weekly', 'monthly'"},
                "channel": {"type": "string", "description": "Where to deliver: 'chat', 'sms', 'email'"},
            },
            "required": ["message", "time"],
        },
        fn=lambda message, time, repeat=None, channel="chat": set_reminder(db, message, time, repeat, channel),
    )

    registry.register(
        name="list_reminders",
        description="List all upcoming reminders for the user.",
        parameters={"type": "object", "properties": {}},
        fn=lambda: list_reminders(db),
    )

    registry.register(
        name="cancel_reminder",
        description="Cancel a reminder by its ID.",
        parameters={
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "The reminder ID to cancel"},
            },
            "required": ["reminder_id"],
        },
        fn=lambda reminder_id: cancel_reminder(db, reminder_id),
    )
