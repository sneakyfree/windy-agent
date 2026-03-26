"""Failure log CRUD operations — the \"Never Wrong Twice\" system.

Tracks user corrections, factual errors, preference misses, and
execution failures so the agent learns from its mistakes.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from windyfly.memory.database import Database


def log_failure(
    db: Database,
    fault_type: str,
    description: str,
    *,
    root_cause: str | None = None,
    correction_action: str | None = None,
    correction_skill_id: str | None = None,
) -> str:
    """Log a failure/correction event.

    Args:
        db: Database instance.
        fault_type: Category ('factual_error', 'preference_miss',
                    'execution_failure', 'ambiguity_mishandled').
        description: Description of what went wrong.
        root_cause: Optional analyzed root cause.
        correction_action: Optional corrective action taken.
        correction_skill_id: Optional linked skill that was created to fix it.

    Returns:
        The generated failure ID.
    """
    failure_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO failures (id, fault_type, description, root_cause,
                              correction_action, correction_skill_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (failure_id, fault_type, description, root_cause,
         correction_action, correction_skill_id),
    )
    db.commit()
    return failure_id


def get_recent_failures(
    db: Database,
    limit: int = 20,
    *,
    fault_type: str | None = None,
    unresolved_only: bool = False,
) -> list[dict]:
    """Get recent failure entries, optionally filtered.

    Args:
        db: Database instance.
        limit: Max entries to return.
        fault_type: Optional filter by type.
        unresolved_only: If True, only return unresolved failures.

    Returns:
        List of failure dicts.
    """
    conditions = []
    params: list = []

    if fault_type:
        conditions.append("fault_type = ?")
        params.append(fault_type)
    if unresolved_only:
        conditions.append("improvement_verified = FALSE")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    return db.fetchall(
        f"SELECT * FROM failures {where} ORDER BY created_at DESC LIMIT ?",
        tuple(params),
    )


def resolve_failure(
    db: Database,
    failure_id: str,
    *,
    correction_action: str | None = None,
    correction_skill_id: str | None = None,
) -> None:
    """Mark a failure as resolved.

    Args:
        db: Database instance.
        failure_id: The failure ID to resolve.
        correction_action: Optional corrective action description.
        correction_skill_id: Optional skill created to fix it.
    """
    db.execute(
        """
        UPDATE failures
        SET improvement_verified = TRUE,
            resolved_at = CURRENT_TIMESTAMP,
            correction_action = COALESCE(?, correction_action),
            correction_skill_id = COALESCE(?, correction_skill_id)
        WHERE id = ?
        """,
        (correction_action, correction_skill_id, failure_id),
    )
    db.commit()


def check_recurring_failure(
    db: Database,
    fault_type: str,
    description: str,
) -> bool:
    """Check if a similar failure occurred in the last 7 days.

    Args:
        db: Database instance.
        fault_type: The failure type to check.
        description: Description to match against.

    Returns:
        True if a similar failure exists recently.
    """
    row = db.fetchone(
        """
        SELECT COUNT(*) as count FROM failures
        WHERE fault_type = ?
          AND description LIKE ?
          AND created_at >= datetime('now', '-7 days')
        """,
        (fault_type, f"%{description[:50]}%"),
    )
    return bool(row and row["count"] > 0)
