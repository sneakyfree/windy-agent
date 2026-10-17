"""Personality versioning — snapshots, diff, drift detection, rollback.

Ensures the user can track how their agent's personality has evolved
and rollback unwanted changes.

Production enhancements:
  - Automatic 24-hour snapshot scheduling
  - Drift detection: alerts when any slider moves >2 points without user action
  - Event logging for drift events
  - Dashboard flagging for drift detection (``drift_alert`` soul key,
    surfaced as ``drift`` in the dashboard summary)
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.soul import get_all_soul, get_soul, upsert_soul

logger = logging.getLogger(__name__)

# A slider must move MORE than this many points to count as drift.
DRIFT_THRESHOLD = 2
# Compare against the personality as it was this many days ago.
DRIFT_BASELINE_DAYS = 30

# ``soul.source`` values that mean "the owner set this value". Every
# owner-facing write path funnels through control_panel.set_slider /
# apply_preset, which write "control_panel": the dashboard slider PUT
# and presets (UDS sliders.set), and the /slider and /preset chat
# commands. A personality rollback (the owner's undo button) writes
# "rollback". Anything else is not an owner action, so a large move under
# it is drift. Since shape-shift was retired (2026-10-10) no code path
# writes sliders under another source; this catches a direct database
# edit or any future non-owner writer.
# "soul_import" is the owner's /soul yes to an imported tone (soul_import/flow.py).
OWNER_SOURCES: frozenset[str] = frozenset({"control_panel", "rollback", "soul_import"})

# Soul key the latest drift result is persisted under (dashboard reads it).
DRIFT_ALERT_KEY = "drift_alert"

_SLIDER_PREFIX = "slider_"


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
        # change), which is what rollback + drift detection already want.
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
    sliders_only: bool = False,
) -> list[dict[str, Any]]:
    """Get recent personality change history.

    Args:
        db: Database instance.
        limit: Max entries to return.
        sliders_only: Only return rows for personality sliders.

    Returns:
        List of soul_history rows (plus the soul ``key`` they belong
        to), most recent first.
    """
    where = "WHERE substr(s.key, 1, 7) = 'slider_'" if sliders_only else ""
    return db.fetchall(
        f"""
        SELECT h.*, s.key AS key
        FROM soul_history h LEFT JOIN soul s ON s.id = h.soul_id
        {where}
        ORDER BY h.created_at DESC, h.rowid DESC LIMIT ?
        """,
        (limit,),
    )


def _baseline_for(
    db: Database, soul_id: str, baseline_days: int,
) -> dict[str, Any] | None:
    """The recorded value of one soul row ~``baseline_days`` ago.

    The baseline is the newest soul_history row at least
    ``baseline_days`` old (soul_history is a change-log, so that row's
    ``new_value`` is what the slider was set to back then). When there is
    no row that old yet — a young install — fall back to the OLDEST row
    we have, i.e. the earliest known value. No history at all → None.
    """
    row = db.fetchone(
        """
        SELECT new_value AS value, created_at FROM soul_history
        WHERE soul_id = ? AND created_at <= datetime('now', ?)
        ORDER BY created_at DESC, rowid DESC LIMIT 1
        """,
        (soul_id, f"-{int(baseline_days)} days"),
    )
    if row is None:
        row = db.fetchone(
            """
            SELECT new_value AS value, created_at FROM soul_history
            WHERE soul_id = ?
            ORDER BY created_at ASC, rowid ASC LIMIT 1
            """,
            (soul_id,),
        )
    return row


def detect_drift(
    db: Database,
    user_id: str = "default",
    baseline_days: int = DRIFT_BASELINE_DAYS,
) -> dict[str, Any] | None:
    """Detect personality drift the owner did not ask for.

    For every slider, compare its current value with its value
    ``baseline_days`` ago (see :func:`_baseline_for` — newest snapshot at
    least that old, else the oldest snapshot available). A slider is
    flagged when it moved more than ``DRIFT_THRESHOLD`` points AND its
    current value was not set by the owner (``soul.source`` not in
    ``OWNER_SOURCES``). An owner moving their own slider is never drift.

    Returns:
        ``{"drifted_sliders": [{name, old, new, delta, source, since}],
        "drift_source": <comma-joined sources>, "baseline_at": <earliest
        baseline timestamp>}`` or None when nothing drifted.
    """
    drifted: list[dict[str, Any]] = []

    for row in get_all_soul(db, user_id=user_id):
        key = row["key"]
        if not key.startswith(_SLIDER_PREFIX):
            continue
        source = row.get("source") or "unknown"
        if source in OWNER_SOURCES:
            continue
        baseline = _baseline_for(db, row["id"], baseline_days)
        if baseline is None:
            continue
        try:
            old_val = int(baseline["value"])
            new_val = int(row["value"])
        except (ValueError, TypeError):
            continue
        delta = abs(new_val - old_val)
        if delta > DRIFT_THRESHOLD:
            drifted.append({
                "name": key[len(_SLIDER_PREFIX):],
                "old": old_val,
                "new": new_val,
                "delta": delta,
                "source": source,
                "since": baseline["created_at"],
            })

    if not drifted:
        return None

    return {
        "drifted_sliders": drifted,
        "drift_source": ",".join(sorted({d["source"] for d in drifted})),
        "baseline_at": min(d["since"] for d in drifted),
    }


def get_drift_alert(db: Database, user_id: str = "default") -> dict[str, Any]:
    """The latest persisted drift result, for the dashboard.

    Always returns a dict with ``detected`` (bool) and ``drifted_sliders``
    (list); ``drift_source`` / ``baseline_at`` are present when detected.
    """
    empty: dict[str, Any] = {"detected": False, "drifted_sliders": []}
    row = get_soul(db, DRIFT_ALERT_KEY, user_id=user_id)
    if row is None:
        return empty
    try:
        data = json.loads(row["value"])
    except (ValueError, TypeError):
        return empty
    if not isinstance(data, dict) or not data.get("detected"):
        return empty
    # Pre-fix alerts stored the list under "sliders".
    sliders = data.get("drifted_sliders", data.get("sliders", []))
    return {
        "detected": True,
        "drifted_sliders": sliders if isinstance(sliders, list) else [],
        "drift_source": data.get("drift_source"),
        "baseline_at": data.get("baseline_at"),
    }


def _clear_drift_alert(db: Database, source: str, user_id: str) -> None:
    upsert_soul(
        db,
        key=DRIFT_ALERT_KEY,
        value=json.dumps({"detected": False, "drifted_sliders": []}),
        source=source,
        user_id=user_id,
    )


def detect_and_log_drift(
    db: Database,
    write_queue: Any = None,
    user_id: str = "default",
) -> dict[str, Any] | None:
    """Detect personality drift, log it, and persist it for the dashboard.

    Drift found → warning log + ``personality_drift`` event + the
    ``drift_alert`` soul key. No drift → a previously raised alert is
    cleared, so the dashboard always shows the latest result.

    Args:
        db: Database instance.
        write_queue: WriteQueue for async event logging.
        user_id: User ID.

    Returns:
        Drift report if drift detected, None otherwise.
    """
    drift = detect_drift(db, user_id=user_id)

    if drift is None:
        try:
            if get_drift_alert(db, user_id=user_id)["detected"]:
                _clear_drift_alert(db, "drift_detector", user_id)
        except Exception as e:
            logger.debug("Could not clear resolved drift alert: %s", e)
        return None

    # Log warnings for each drifted slider
    for slider in drift["drifted_sliders"]:
        logger.warning(
            "Personality drift detected: %s moved from %d to %d (source=%s)",
            slider["name"],
            slider["old"],
            slider["new"],
            slider["source"],
        )

    # Record in events table
    if write_queue is not None:
        try:
            from windyfly.observability.events import log_event
            log_event(db, write_queue, "personality_drift", {
                "drifted_sliders": drift["drifted_sliders"],
                "drift_source": drift["drift_source"],
                "baseline_at": drift["baseline_at"],
                "user_id": user_id,
            })
        except Exception as e:
            logger.debug("Could not log drift event: %s", e)

    # Persist for the dashboard (get_dashboard_summary → "drift").
    # Deterministic value (no check timestamp) so the daily snapshot
    # does not record a new soul_history row for an unchanged alert.
    try:
        upsert_soul(
            db,
            key=DRIFT_ALERT_KEY,
            value=json.dumps({
                "detected": True,
                "drifted_sliders": drift["drifted_sliders"],
                "drift_source": drift["drift_source"],
                "baseline_at": drift["baseline_at"],
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
    """24-hour periodic drift check: detect, THEN snapshot.

    Called by the decay scheduler every 24 hours (or on demand).

    1. Compare current sliders to the ~30-day-old baseline
    2. If a slider moved >2 points without owner action → log, record,
       flag for the dashboard (or clear a resolved flag)
    3. Snapshot current sliders (recorded only when changed)

    Order matters: snapshotting first made the baseline equal to the
    current value, so drift could never fire.

    Args:
        db: Database instance.
        write_queue: WriteQueue for async event logging.
        user_id: User ID.

    Returns:
        Dict with snapshot_id and optional drift report.
    """
    drift = detect_and_log_drift(db, write_queue, user_id=user_id)
    snapshot_id = snapshot_personality(db, user_id=user_id, changed_by="periodic")

    return {
        "snapshot_id": snapshot_id,
        "drift_detected": drift is not None,
        "drift_report": drift,
    }


def plan_personality_rollback(
    db: Database,
    snapshot_date: str,
    user_id: str = "default",
) -> list[dict[str, Any]]:
    """What a rollback to ``snapshot_date`` would change, without changing it.

    For each slider, the target is the value recorded by the newest
    soul_history row at or before ``snapshot_date`` (its ``new_value`` —
    the value the slider held from then on). Only ``slider_*`` rows are
    considered; sliders that already hold their target are left out.

    Returns:
        One entry per slider that would change, sorted by name:
        ``{"slider", "current", "restore", "soul_id"}``.
    """
    entries = db.fetchall(
        """
        SELECT h.soul_id, h.new_value, s.key, s.value AS current_value
        FROM soul_history h JOIN soul s ON s.id = h.soul_id
        WHERE h.created_at <= ? AND s.user_id = ?
          AND substr(s.key, 1, 7) = 'slider_'
        ORDER BY h.created_at DESC, h.rowid DESC
        """,
        (snapshot_date, user_id),
    )

    seen_souls: set[str] = set()
    changes: list[dict[str, Any]] = []
    for entry in entries:
        soul_id = entry["soul_id"]
        if soul_id in seen_souls:
            continue
        seen_souls.add(soul_id)
        target = entry["new_value"]
        if target is None or target == entry["current_value"]:
            continue
        changes.append({
            "slider": entry["key"][len(_SLIDER_PREFIX):],
            "current": entry["current_value"],
            "restore": target,
            "soul_id": soul_id,
        })
    return sorted(changes, key=lambda c: c["slider"])


class RollbackPlanChanged(Exception):
    """The sliders changed after the owner saw the list; nothing was restored."""

    def __init__(self, changes: list[dict[str, Any]]) -> None:
        super().__init__("the personality changed since the restore list was shown")
        self.changes = changes


def public_changes(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The owner-facing part of a plan: slider, current, restore (no internal ids)."""
    return [{k: c[k] for k in ("slider", "current", "restore")} for c in plan]


def rollback_personality(
    db: Database,
    snapshot_date: str,
    user_id: str = "default",
    *,
    expected: list[dict[str, Any]] | None = None,
) -> int:
    """Rollback the personality sliders to how they were at a point in time.

    Applies exactly ``plan_personality_rollback`` (the owner sees that list
    before confirming). With ``expected`` (the list the owner confirmed), it
    refuses with RollbackPlanChanged and writes nothing if the plan is no
    longer that list. Identity/bookkeeping soul keys are never touched.
    Restored rows are marked ``source="rollback"`` (an owner action) and any
    drift alert is cleared.

    Args:
        db: Database instance.
        snapshot_date: ISO date/datetime string to rollback to.
        user_id: User ID.
        expected: The changes the owner confirmed (slider/current/restore).

    Returns:
        Number of slider values actually changed.
    """
    changes = plan_personality_rollback(db, snapshot_date, user_id)
    if expected is not None and public_changes(changes) != expected:
        raise RollbackPlanChanged(public_changes(changes))
    for change in changes:
        db.execute(
            """
            INSERT INTO soul_history (id, soul_id, old_value, new_value, changed_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), change["soul_id"], change["current"], change["restore"], "rollback"),
        )
        db.execute(
            """
            UPDATE soul SET value = ?, source = 'rollback',
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (change["restore"], change["soul_id"]),
        )

    try:
        _clear_drift_alert(db, "rollback", user_id)
    except Exception as e:
        logger.debug("Could not clear drift alert on rollback: %s", e)

    db.commit()
    return len(changes)
