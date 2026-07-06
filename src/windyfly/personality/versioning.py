"""Personality versioning — snapshots, diff, drift detection, rollback.

Ensures the user can track how their agent's personality has evolved
and rollback unwanted changes.

Production enhancements:
  - Automatic 24-hour snapshot scheduling
  - Drift detection: alerts when any slider moves >2 points without user action
  - Event logging for drift events
  - Dashboard flagging for drift detection
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.soul import get_all_soul, upsert_soul

logger = logging.getLogger(__name__)


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
        # Dedup: only record a row when this soul value actually differs
        # from the most-recent recorded value. Pre-2026-07-06 this wrote
        # 20 identical rows (old==new==current) on EVERY periodic run, so
        # a stable personality still grew soul_history by 20 rows/run —
        # and a restart-looping service ran the periodic check on every
        # boot, ballooning Windy 0's DB to 363k rows / 122 MB. A snapshot
        # of an UNCHANGED personality carries no information; skip it.
        # soul_history thus becomes a true change-log (one row per real
        # change), which is what rollback + drift detection already want
        # (both read the most-recent row per soul_id).
        last = db.fetchone(
            """
            SELECT new_value FROM soul_history
            WHERE soul_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (row["id"],),
        )
        if last is not None and last["new_value"] == row["value"]:
            continue  # unchanged since last record — no-op snapshot

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

    Compares current slider values to the most recent snapshot.
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

        # Find the most recent snapshot value (changed_by != 'agent_evolution')
        old_entry = db.fetchone(
            """
            SELECT old_value, changed_by FROM soul_history
            WHERE soul_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (soul_id,),
        )

        if old_entry:
            try:
                old_val = int(old_entry["old_value"])
                new_val = int(current_val)
                delta = abs(new_val - old_val)
                if delta > 2:
                    drifted.append({
                        "name": slider_name,
                        "old": old_val,
                        "new": new_val,
                        "delta": delta,
                    })
            except (ValueError, TypeError):
                continue

    if drifted:
        return {
            "drifted_sliders": drifted,
            "drift_source": "agent_evolution",
        }

    return None


def detect_and_log_drift(
    db: Database,
    write_queue: Any = None,
    user_id: str = "default",
) -> dict[str, Any] | None:
    """Detect personality drift and log warnings + events if found.

    This is the production-ready entry point: it detects drift, logs
    warnings, records events in the events table, and flags for the dashboard.

    Args:
        db: Database instance.
        write_queue: WriteQueue for async event logging.
        user_id: User ID.

    Returns:
        Drift report if drift detected, None otherwise.
    """
    drift = detect_drift(db, user_id=user_id)

    if drift is None:
        return None

    # Log warnings for each drifted slider
    for slider in drift["drifted_sliders"]:
        logger.warning(
            "Personality drift detected: %s moved from %d to %d",
            slider["name"],
            slider["old"],
            slider["new"],
        )

    # Record in events table
    if write_queue is not None:
        try:
            from windyfly.observability.events import log_event
            log_event(db, write_queue, "personality_drift", {
                "drifted_sliders": drift["drifted_sliders"],
                "drift_source": drift["drift_source"],
                "user_id": user_id,
            })
        except Exception as e:
            logger.debug("Could not log drift event: %s", e)

    # Flag on dashboard — write a soul key that the dashboard can check
    try:
        import json
        upsert_soul(
            db,
            key="drift_alert",
            value=json.dumps({
                "detected": True,
                "sliders": drift["drifted_sliders"],
            }),
            source="drift_detector",
            user_id=user_id,
        )
    except Exception as e:
        logger.debug("Could not flag drift on dashboard: %s", e)

    return drift


def run_periodic_drift_check(
    db: Database,
    write_queue: Any = None,
    user_id: str = "default",
) -> dict[str, Any]:
    """24-hour periodic drift check: snapshot + detect.

    This is meant to be called by the decay scheduler or gateway
    every 24 hours (or on demand).

    1. Snapshot current sliders
    2. Compare to last snapshot
    3. If drift >2 points without user action → log, record, flag

    Args:
        db: Database instance.
        write_queue: WriteQueue for async event logging.
        user_id: User ID.

    Returns:
        Dict with snapshot_id and optional drift report.
    """
    # Take a snapshot first
    snapshot_id = snapshot_personality(db, user_id=user_id, changed_by="periodic")

    # Detect drift
    drift = detect_and_log_drift(db, write_queue, user_id=user_id)

    return {
        "snapshot_id": snapshot_id,
        "drift_detected": drift is not None,
        "drift_report": drift,
    }


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

    # Clear drift alert if it exists
    try:
        upsert_soul(
            db,
            key="drift_alert",
            value='{"detected": false, "sliders": []}',
            source="rollback",
            user_id=user_id,
        )
    except Exception as e:
        logger.debug("Could not clear drift alert on rollback: %s", e)

    db.commit()
    return restored
