"""Structured event logging — observability layer.

Logs all system events to an events table with automatic 30-day pruning.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.write_queue import Priority, WriteQueue

logger = logging.getLogger(__name__)

# All known event types
EVENT_TYPES = frozenset({
    "agent.respond",
    "memory.write",
    "skill.evaluate",
    "cost.log",
    "failure.detect",
    "intent.surface",
    "conflict.detect",
    "decay.run",
    "matrix.message",
    "matrix.reconnect",
    "personality.change",
    "offline.fallback",
    "sub_agent.spawn",
    "shape_shift.enter",
    "shape_shift.exit",
    "shape_shift.tool",
    "shape_shift.restore",
    "sms.inbound",
    "sms.outbound",
    "sms.optout",
    "email.inbound",
    "email.outbound",
})


def log_event(
    db: Database,
    write_queue: WriteQueue,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Log a structured event.

    Args:
        db: Database instance.
        write_queue: WriteQueue for async writes.
        event_type: Event type string.
        data: Event data dict.
    """
    def _write():
        if event_type not in EVENT_TYPES:
            logger.warning("Unknown event type: %s — consider adding to EVENT_TYPES", event_type)
        db.execute(
            "INSERT INTO events (event_type, data) VALUES (?, ?)",
            (event_type, json.dumps(data)),
        )
        # Prune old events (> 30 days)
        db.execute(
            "DELETE FROM events WHERE created_at < datetime('now', '-30 days')"
        )
        db.commit()

    write_queue.enqueue(Priority.LOW, _write)


def get_recent_events(
    db: Database,
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Get recent events, optionally filtered by type.

    Args:
        db: Database instance.
        event_type: Optional filter.
        limit: Max events to return.

    Returns:
        List of event dicts.
    """
    if event_type:
        rows = db.fetchall(
            "SELECT * FROM events WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
            (event_type, limit),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    # Parse JSON data field
    for row in rows:
        if isinstance(row.get("data"), str):
            try:
                row["data"] = json.loads(row["data"])
            except (json.JSONDecodeError, TypeError):
                pass

    return rows


def get_event_counts(
    db: Database,
    since_hours: int = 24,
) -> dict[str, int]:
    """Get event counts by type for the last N hours.

    Args:
        db: Database instance.
        since_hours: Lookback window in hours.

    Returns:
        Dict of event_type → count.
    """
    rows = db.fetchall(
        """
        SELECT event_type, COUNT(*) as c
        FROM events
        WHERE created_at > datetime('now', ? || ' hours')
        GROUP BY event_type
        """,
        (f"-{since_hours}",),
    )
    return {row["event_type"]: row["c"] for row in rows}
