"""Helpers for the agent_actions ledger (Wave 2 #2).

The ledger is dual-purpose: it's an audit trail of every capability
invocation (who called what, when, with what args, did it succeed)
AND the substrate the Wave 7 outcome optimizer will train on. The
schema includes nullable columns (``intent_id``, ``parent_action_id``,
``outcome_score``) reserved for the optimizer so we don't have to
migrate live data later.

Writes go through the priority WriteQueue at HIGH priority — capability
audits shouldn't sit behind episode batches because /pulse and
debugging both need fresh data.
"""

from __future__ import annotations

import logging
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.write_queue import Priority, WriteQueue

logger = logging.getLogger(__name__)


def record_action_start(
    db: Database,
    write_queue: WriteQueue,
    *,
    action_id: str,
    capability_id: str,
    tier: int,
    band: str,
    sandbox_tier: str,
    args_json: str | None,
    started_at: str,
    session_id: str | None = None,
    user_id: str | None = None,
    intent_id: str | None = None,
    parent_action_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Insert the opening row for a capability invocation.

    The corresponding ``record_action_end`` updates the same row when
    the handler returns or raises.

    ``request_id`` (Wave 14) is captured from the contextvar at enqueue
    time so the audit row reflects the originating request even though
    the actual write happens on the WriteQueue thread (which has its
    own context).
    """
    if request_id is None:
        from windyfly.agent.tracing import get_request_id
        request_id = get_request_id()
    write_queue.enqueue(
        Priority.HIGH,
        _do_insert_start,
        db,
        action_id, capability_id, tier, band, sandbox_tier,
        args_json, started_at, session_id, user_id,
        intent_id, parent_action_id, request_id,
    )


def _do_insert_start(
    db: Database,
    action_id: str,
    capability_id: str,
    tier: int,
    band: str,
    sandbox_tier: str,
    args_json: str | None,
    started_at: str,
    session_id: str | None,
    user_id: str | None,
    intent_id: str | None,
    parent_action_id: str | None,
    request_id: str | None,
) -> None:
    try:
        db.execute(
            """
            INSERT INTO agent_actions (
                id, capability_id, tier, band, sandbox_tier,
                args_json, started_at, session_id, user_id,
                intent_id, parent_action_id, request_id, success
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                action_id, capability_id, tier, band, sandbox_tier,
                args_json, started_at, session_id, user_id,
                intent_id, parent_action_id, request_id,
            ),
        )
    except Exception as e:
        # Audit failures should never break the invocation itself —
        # the agent loop already returned through finally by now, but
        # we still want to log the lost row for postmortem.
        logger.warning("agent_actions start insert failed for %s: %s", action_id, e)


def record_action_end(
    db: Database,
    write_queue: WriteQueue,
    *,
    action_id: str,
    success: bool,
    duration_ms: int,
    error_class: str | None = None,
    error_message: str | None = None,
    cost_usd: float = 0.0,
    ended_at: str,
    outcome_score: float | None = None,
) -> None:
    """Update the action row with final outcome fields."""
    write_queue.enqueue(
        Priority.HIGH,
        _do_update_end,
        db,
        action_id, 1 if success else 0, duration_ms,
        error_class, error_message, cost_usd, ended_at, outcome_score,
    )


def _do_update_end(
    db: Database,
    action_id: str,
    success: int,
    duration_ms: int,
    error_class: str | None,
    error_message: str | None,
    cost_usd: float,
    ended_at: str,
    outcome_score: float | None,
) -> None:
    try:
        db.execute(
            """
            UPDATE agent_actions
            SET success = ?,
                duration_ms = ?,
                error_class = ?,
                error_message = ?,
                cost_usd = ?,
                ended_at = ?,
                outcome_score = COALESCE(?, outcome_score)
            WHERE id = ?
            """,
            (
                success, duration_ms, error_class, error_message,
                cost_usd, ended_at, outcome_score, action_id,
            ),
        )
    except Exception as e:
        logger.warning("agent_actions end update failed for %s: %s", action_id, e)


def get_recent_actions(
    db: Database, limit: int = 20,
) -> list[dict[str, Any]]:
    return db.fetchall(
        "SELECT * FROM agent_actions ORDER BY started_at DESC LIMIT ?",
        (limit,),
    )


def get_actions_for_capability(
    db: Database, capability_id: str, limit: int = 20,
) -> list[dict[str, Any]]:
    return db.fetchall(
        """
        SELECT * FROM agent_actions
        WHERE capability_id = ?
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (capability_id, limit),
    )


def get_failed_actions(
    db: Database, limit: int = 20,
) -> list[dict[str, Any]]:
    return db.fetchall(
        """
        SELECT * FROM agent_actions
        WHERE success = 0 AND ended_at IS NOT NULL
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (limit,),
    )


def capability_success_rate(
    db: Database, capability_id: str,
) -> dict[str, Any]:
    """Wave 7 optimizer will read this. Returns total / successes / rate."""
    row = db.fetchone(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successes,
            AVG(duration_ms) AS avg_duration_ms,
            AVG(cost_usd) AS avg_cost_usd
        FROM agent_actions
        WHERE capability_id = ? AND ended_at IS NOT NULL
        """,
        (capability_id,),
    )
    if not row or not row["total"]:
        return {
            "capability_id": capability_id,
            "total": 0,
            "successes": 0,
            "success_rate": 0.0,
            "avg_duration_ms": 0,
            "avg_cost_usd": 0.0,
        }
    total = row["total"]
    successes = row["successes"] or 0
    return {
        "capability_id": capability_id,
        "total": total,
        "successes": successes,
        "success_rate": successes / total if total else 0.0,
        "avg_duration_ms": row["avg_duration_ms"] or 0,
        "avg_cost_usd": row["avg_cost_usd"] or 0.0,
    }
