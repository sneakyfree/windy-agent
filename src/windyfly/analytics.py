"""Agent usage analytics — track how the agent is used.

Stores events locally in SQLite. Optionally syncs to Windy Cloud.
Used by the dashboard Home page and cost reports.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from windyfly.memory.database import Database

logger = logging.getLogger(__name__)

_ANALYTICS_SQL = """
CREATE TABLE IF NOT EXISTS analytics_events (
    id TEXT PRIMARY KEY,
    event TEXT NOT NULL,
    properties TEXT DEFAULT '{}',
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def _ensure_table(db: Database) -> None:
    db.conn.executescript(_ANALYTICS_SQL)


def track(db: Database, event: str, properties: dict[str, Any] | None = None) -> None:
    """Store an analytics event locally.

    Args:
        db: Database instance.
        event: Event name (e.g. 'message_received', 'tool_invoked').
        properties: Optional dict of event properties.
    """
    import json

    _ensure_table(db)
    event_id = str(uuid.uuid4())[:8]
    props_json = json.dumps(properties or {})
    try:
        db.execute(
            "INSERT INTO analytics_events (id, event, properties, timestamp) VALUES (?, ?, ?, ?)",
            (event_id, event, props_json, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
    except Exception as e:
        logger.debug("Analytics tracking failed: %s", e)


def get_daily_stats(db: Database) -> dict[str, Any]:
    """Get today's usage statistics."""
    import json

    _ensure_table(db)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    messages_in = _count_events(db, "message_received", today)
    messages_out = _count_events(db, "message_sent", today)
    tool_calls = _count_events(db, "tool_invoked", today)

    # Get tool breakdown
    tool_rows = db.fetchall(
        "SELECT properties FROM analytics_events WHERE event = 'tool_invoked' AND timestamp >= ?",
        (f"{today}T00:00:00",),
    )
    tool_usage: dict[str, int] = {}
    for row in tool_rows:
        try:
            props = json.loads(row["properties"])
            name = props.get("tool_name", "unknown")
            tool_usage[name] = tool_usage.get(name, 0) + 1
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "date": today,
        "messages_received": messages_in,
        "messages_sent": messages_out,
        "tool_calls": tool_calls,
        "tool_usage": tool_usage,
    }


def get_weekly_stats(db: Database) -> list[dict[str, Any]]:
    """Get daily stats for the past 7 days."""
    _ensure_table(db)
    days = []
    for i in range(7):
        from datetime import timedelta
        date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        days.append({
            "date": date,
            "messages": _count_events(db, "message_received", date),
            "tool_calls": _count_events(db, "tool_invoked", date),
        })
    return days


def _count_events(db: Database, event: str, date: str) -> int:
    """Count events of a type on a given date."""
    row = db.fetchone(
        "SELECT COUNT(*) as cnt FROM analytics_events WHERE event = ? AND timestamp >= ? AND timestamp < ?",
        (event, f"{date}T00:00:00", f"{date}T23:59:59"),
    )
    return row["cnt"] if row else 0
