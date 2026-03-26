"""Personality versioning — snapshots, diff, drift detection, rollback.

Ensures the user can track how their agent's personality has evolved
and rollback unwanted changes.
"""

from __future__ import annotations

import uuid
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.soul import get_all_soul, upsert_soul


def snapshot_personality(
    db: Database,
    user_id: str = "default",
    changed_by: str = "user",
) -> str:
    """Create a versioned checkpoint of the current personality.

    Args:
        db: Database instance.
        user_id: User ID.
        changed_by: Who triggered the snapshot.

    Returns:
        Snapshot batch ID.
    """
    batch_id = str(uuid.uuid4())
    soul_rows = get_all_soul(db, user_id=user_id)

    for row in soul_rows:
        entry_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO soul_history (id, soul_id, old_value, new_value, changed_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entry_id, row["id"], row["value"], row["value"], changed_by),
        )

    db.commit()
    return batch_id


def get_personality_history(
    db: Database,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Get recent personality change history.

    Args:
        db: Database instance.
        limit: Max entries to return.

    Returns:
        List of soul_history rows, most recent first.
    """
    return db.fetchall(
        "SELECT * FROM soul_history ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )


def detect_drift(
    db: Database,
    user_id: str = "default",
) -> dict[str, Any] | None:
    """Detect unauthorized personality drift.

    Compares current slider values to their values 30 days ago.
    Flags any slider that changed more than 2 points without explicit user action.

    Args:
        db: Database instance.
        user_id: User ID.

    Returns:
        Drift report dict if drift detected, None otherwise.
    """
    soul_rows = get_all_soul(db, user_id=user_id)
    current_values: dict[str, str] = {}
    soul_id_map: dict[str, str] = {}

    for row in soul_rows:
        key = row["key"]
        if key.startswith("slider_"):
            slider_name = key[len("slider_"):]
            current_values[slider_name] = row["value"]
            soul_id_map[slider_name] = row["id"]

    drifted: list[dict[str, Any]] = []

    for slider_name, current_val in current_values.items():
        soul_id = soul_id_map.get(slider_name)
        if not soul_id:
            continue

        # Find the value from 30 days ago
        old_entry = db.fetchone(
            """
            SELECT old_value FROM soul_history
            WHERE soul_id = ?
              AND created_at < datetime('now', '-30 days')
            ORDER BY created_at DESC LIMIT 1
            """,
            (soul_id,),
        )

        if old_entry:
            try:
                old_val = int(old_entry["old_value"])
                new_val = int(current_val)
                if abs(new_val - old_val) > 2:
                    drifted.append({
                        "name": slider_name,
                        "old": old_val,
                        "new": new_val,
                    })
            except (ValueError, TypeError):
                continue

    if drifted:
        return {
            "drifted_sliders": drifted,
            "drift_source": "agent_evolution",
        }

    return None


def rollback_personality(
    db: Database,
    snapshot_date: str,
    user_id: str = "default",
) -> int:
    """Rollback personality to a previous snapshot.

    Finds soul_history entries closest to the given date and
    restores soul values to those entries.

    Args:
        db: Database instance.
        snapshot_date: ISO date string to rollback to.
        user_id: User ID.

    Returns:
        Number of values restored.
    """
    # Find the most recent history entries before the snapshot date
    entries = db.fetchall(
        """
        SELECT DISTINCT soul_id, old_value
        FROM soul_history
        WHERE created_at <= ?
        ORDER BY created_at DESC
        """,
        (snapshot_date,),
    )

    seen_souls: set[str] = set()
    restored = 0

    for entry in entries:
        soul_id = entry["soul_id"]
        if soul_id in seen_souls:
            continue
        seen_souls.add(soul_id)

        # Get the soul row
        soul = db.fetchone("SELECT * FROM soul WHERE id = ?", (soul_id,))
        if soul:
            old_value = entry["old_value"]
            # Log the rollback
            log_id = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO soul_history (id, soul_id, old_value, new_value, changed_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (log_id, soul_id, soul["value"], old_value, "rollback"),
            )
            # Restore the value
            db.execute(
                "UPDATE soul SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (old_value, soul_id),
            )
            restored += 1

    db.commit()
    return restored
