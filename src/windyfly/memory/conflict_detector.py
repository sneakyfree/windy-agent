"""Conflict detector — detect and resolve contradictions in knowledge.

When a new fact contradicts an existing one, create a conflict record
and surface it to the user for resolution.
"""

from __future__ import annotations

import uuid
from typing import Any

from windyfly.memory.database import Database


def check_for_conflict(
    db: Database,
    node_type: str,
    node_name: str,
    new_value: str,
) -> dict[str, Any] | None:
    """Check if a new value contradicts an existing node.

    Args:
        db: Database instance.
        node_type: Node type.
        node_name: Node name.
        new_value: The new proposed value/metadata.

    Returns:
        Conflict dict if found, None otherwise.
    """
    existing = db.fetchone(
        "SELECT * FROM nodes WHERE type = ? AND name = ?",
        (node_type, node_name),
    )

    if not existing:
        return None

    old_value = existing.get("metadata") or ""
    if isinstance(old_value, dict):
        import json
        old_value = json.dumps(old_value)

    # Simple conflict: values differ
    if old_value and old_value != new_value and new_value:
        conflict_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO conflicts (id, node_id, old_value, new_value)
            VALUES (?, ?, ?, ?)
            """,
            (conflict_id, existing["id"], str(old_value), str(new_value)),
        )
        db.commit()

        return {
            "conflict_id": conflict_id,
            "node_id": existing["id"],
            "old_value": old_value,
            "new_value": new_value,
        }

    return None


def resolve_conflict(
    db: Database,
    conflict_id: str,
    resolution: str,
    keep_new: bool,
) -> None:
    """Resolve a conflict.

    Args:
        db: Database instance.
        conflict_id: Conflict to resolve.
        resolution: Resolution description.
        keep_new: If True, update the node with the new value.
    """
    conflict = db.fetchone(
        "SELECT * FROM conflicts WHERE id = ?",
        (conflict_id,),
    )
    if not conflict:
        return

    db.execute(
        """
        UPDATE conflicts SET
            resolution_status = 'user_resolved',
            user_resolved = TRUE,
            resolved_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (conflict_id,),
    )

    if keep_new and conflict["node_id"]:
        db.execute(
            "UPDATE nodes SET metadata = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conflict["new_value"], conflict["node_id"]),
        )

    db.commit()


def get_unresolved_conflicts(db: Database) -> list[dict[str, Any]]:
    """Get all unresolved conflicts."""
    return db.fetchall(
        """
        SELECT * FROM conflicts
        WHERE resolution_status = 'unresolved'
        ORDER BY created_at DESC
        """,
    )
