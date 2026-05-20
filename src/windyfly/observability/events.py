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
    "personality_drift",
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
    "agent.confabulation_detected",
    "agent.empty_after_tools",
    # Lifeboat / resurrection lifecycle (PRs #138, #145, #160, #161)
    "first_contact.welcome",
    "resurrect.dispatch",
    "auto_resurrect.fired",
    "auto_resurrect.skipped",
    "offline.chain_exhausted",
    "lifeboat.exited",
    "lifeboat.recovery_failed",
    "lifeboat.escaped_wedged",
    # /goal slash command (windy-agent feature parity with Claude
    # Code 2.1.139, Codex CLI, Hermes Agent 0.13.0)
    "goal.set",
    "goal.evaluated",
    "goal.completed",
    "goal.abandoned",
    "goal.expired",
    # Permanent-auth short-circuit (don't-auto-resurrect-on-401)
    "auth.permanent_failure",
    # Provider-native web search lifecycle (PR #164)
    "web_search.native_enabled",
    "web_search.native_skipped",
    "web_search.native_unsupported",
    "web_search.native_used",
    # Write-intent-not-executed tripwire (PR #165)
    "agent.write_intent_unexecuted",
})


def log_event(
    db: Database,
    write_queue: WriteQueue,
    event_type: str,
    data: dict[str, Any],
    *,
    request_id: str | None = None,
) -> None:
    """Log a structured event.

    Args:
        db: Database instance.
        write_queue: WriteQueue for async writes.
        event_type: Event type string.
        data: Event data dict.
        request_id: Optional Wave 14 tracing correlation id. Captured
            from contextvar at enqueue time so the writer thread sees
            the originating request, not its own (write_queue runs on
            a different thread where the contextvar would be None).
    """
    if request_id is None:
        from windyfly.agent.tracing import get_request_id
        request_id = get_request_id()

    def _write():
        if event_type not in EVENT_TYPES:
            logger.warning("Unknown event type: %s — consider adding to EVENT_TYPES", event_type)
        db.execute(
            "INSERT INTO events (event_type, data, request_id) VALUES (?, ?, ?)",
            (event_type, json.dumps(data), request_id),
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
