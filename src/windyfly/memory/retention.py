"""Retention for high-churn bookkeeping tables — soul_history + events.

Runs on the 24h decay scheduler (main.py). Two tables grow without
bound and have no product value once stale:

- **soul_history**: pre-2026-07-06 the periodic drift check wrote 20
  identical rows (old==new==current) on EVERY run, and a restart-looping
  service ran that check on every boot — Windy 0 hit 363k rows / 122 MB,
  which then broke cloud backup (the DB was too big to upload). The
  snapshot writer is now deduped (personality/versioning.py), so no NEW
  duplicates appear; `collapse_soul_history` cleans the historical/edge
  duplicates by keeping only *transitions* (plus the latest value per
  soul_id), turning the heartbeat log into a true change-log. This is
  loss-free for the two consumers: rollback resolves a value-at-date as
  the last transition ≤ date, and drift detection reads the latest row
  per soul_id — both preserved.

- **events**: observability breadcrumbs (decay.run, auth failures,
  web_search flags). 68k rows on Windy 0. Keep a recent window; drop the
  rest.

VACUUM (which reclaims file space — SQLite never shrinks on its own)
runs only when a prune actually deleted a meaningful number of rows, so
the 24h cycle doesn't rewrite the whole DB for nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from windyfly.memory.database import Database

logger = logging.getLogger(__name__)

# Keep this many days of raw events. 30d is plenty for the dashboard /
# recent-activity views; older breadcrumbs have no consumer.
_EVENTS_RETENTION_DAYS = 30

# VACUUM is a full-DB rewrite — only worth it after a real cleanup.
_VACUUM_MIN_DELETED = 500


def collapse_soul_history(db: Database) -> int:
    """Delete consecutive-duplicate soul_history rows, per soul_id.

    Keeps the FIRST row of each run of identical ``new_value`` (the
    moment the value changed to that) plus, implicitly, the latest value
    per soul_id (it's the first of its own trailing run). Idempotent:
    with the deduped writer there are no new consecutive duplicates, so
    steady-state this deletes 0.

    Returns the number of rows deleted.
    """
    # Window functions require SQLite 3.25+; every supported runtime has
    # it. LAG over (soul_id ordered by time) finds each row's predecessor
    # within the same soul_id; a row whose value equals its predecessor's
    # is a redundant heartbeat and is dropped.
    cur = db.execute(
        """
        DELETE FROM soul_history
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       new_value,
                       LAG(new_value) OVER (
                           PARTITION BY soul_id ORDER BY created_at, rowid
                       ) AS prev_value
                FROM soul_history
            )
            WHERE prev_value IS NOT NULL AND new_value = prev_value
        )
        """
    )
    db.commit()
    return cur.rowcount if cur.rowcount is not None else 0


def prune_events(db: Database, retention_days: int = _EVENTS_RETENTION_DAYS) -> int:
    """Delete events older than ``retention_days``. Returns rows deleted."""
    cur = db.execute(
        "DELETE FROM events WHERE created_at < datetime('now', ?)",
        (f"-{int(retention_days)} days",),
    )
    db.commit()
    return cur.rowcount if cur.rowcount is not None else 0


def run_retention(db: Database, config: dict[str, Any] | None = None) -> dict[str, int]:
    """Prune high-churn bookkeeping tables; VACUUM if it freed real space.

    Wired into the 24h decay scheduler. Never raises out — a retention
    hiccup must not take down the scheduler (which also runs decay,
    drift, and backup).
    """
    result = {"soul_history_deleted": 0, "events_deleted": 0, "vacuumed": 0}
    try:
        result["soul_history_deleted"] = collapse_soul_history(db)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("soul_history collapse failed: %s", e)
    try:
        days = _EVENTS_RETENTION_DAYS
        if config:
            days = int(config.get("memory", {}).get("events_retention_days", days))
        result["events_deleted"] = prune_events(db, days)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("events prune failed: %s", e)

    total_deleted = result["soul_history_deleted"] + result["events_deleted"]
    if total_deleted >= _VACUUM_MIN_DELETED:
        try:
            db.commit()  # VACUUM cannot run inside a transaction
            db.execute("VACUUM")
            result["vacuumed"] = 1
            logger.info(
                "Retention VACUUM after freeing %d rows (soul_history=%d, events=%d)",
                total_deleted, result["soul_history_deleted"], result["events_deleted"],
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("VACUUM failed: %s", e)
    return result
