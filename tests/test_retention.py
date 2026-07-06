"""Retention for soul_history + events (2026-07-06 bloat fix).

Windy 0's DB hit 363k soul_history rows / 122 MB because the periodic
drift check wrote 20 identical rows every run (amplified by a
restart-looping service), which then broke cloud backup. These tests
pin the two-part fix: (1) the snapshot writer dedups (no row when
nothing changed); (2) run_retention collapses legacy duplicates + prunes
stale events without losing rollback/drift information.
"""

from __future__ import annotations

from windyfly.control_panel import set_slider
from windyfly.memory.database import Database
from windyfly.memory.retention import (
    collapse_soul_history,
    prune_events,
    run_retention,
)
from windyfly.personality.versioning import snapshot_personality


def _soul_history_count(db: Database) -> int:
    return db.fetchone("SELECT COUNT(*) AS c FROM soul_history")["c"]


class TestSnapshotDedup:
    def test_unchanged_snapshot_writes_no_new_rows(self):
        db = Database(":memory:")
        set_slider(db, "humor", 7)
        snapshot_personality(db, changed_by="periodic")  # baseline
        baseline = _soul_history_count(db)
        assert baseline >= 1
        # Ten more periodic runs with NO change → zero new rows (this is
        # the 20-rows-per-run bloat that filled Windy 0's DB).
        for _ in range(10):
            snapshot_personality(db, changed_by="periodic")
        assert _soul_history_count(db) == baseline
        db.close()

    def test_changed_slider_records_exactly_one_new_row(self):
        db = Database(":memory:")
        set_slider(db, "humor", 5)
        snapshot_personality(db)
        before = _soul_history_count(db)
        set_slider(db, "humor", 9)
        snapshot_personality(db)
        assert _soul_history_count(db) == before + 1
        db.close()


class TestCollapse:
    def test_collapse_removes_consecutive_duplicates_keeps_transitions(self):
        db = Database(":memory:")
        # Simulate the LEGACY writer: many identical heartbeat rows with
        # a couple of real transitions interleaved.
        set_slider(db, "humor", 3)
        for _ in range(50):
            db.execute(
                "INSERT INTO soul_history (id, soul_id, old_value, new_value, changed_by) "
                "VALUES (hex(randomblob(8)), 'humor', '3', '3', 'periodic')"
            )
        # a real change → 5, then more heartbeats at 5
        db.execute(
            "INSERT INTO soul_history (id, soul_id, old_value, new_value, changed_by) "
            "VALUES (hex(randomblob(8)), 'humor', '5', '5', 'periodic')"
        )
        for _ in range(50):
            db.execute(
                "INSERT INTO soul_history (id, soul_id, old_value, new_value, changed_by) "
                "VALUES (hex(randomblob(8)), 'humor', '5', '5', 'periodic')"
            )
        db.commit()
        assert _soul_history_count(db) == 101

        deleted = collapse_soul_history(db)
        # Only the two transition points survive for soul_id 'humor'
        # (first '3', first '5'); the 99 duplicates are gone.
        rows = db.fetchall(
            "SELECT new_value FROM soul_history WHERE soul_id='humor' ORDER BY created_at, rowid"
        )
        assert [r["new_value"] for r in rows] == ["3", "5"]
        assert deleted == 99
        db.close()

    def test_collapse_is_idempotent(self):
        db = Database(":memory:")
        set_slider(db, "humor", 4)
        snapshot_personality(db)
        set_slider(db, "humor", 8)
        snapshot_personality(db)
        assert collapse_soul_history(db) == 0  # deduped writer → nothing to collapse
        db.close()

    def test_collapse_preserves_latest_value_for_drift_baseline(self):
        db = Database(":memory:")
        set_slider(db, "humor", 2)
        snapshot_personality(db)
        # heartbeat dupes at the current value
        for _ in range(20):
            db.execute(
                "INSERT INTO soul_history (id, soul_id, old_value, new_value, changed_by) "
                "VALUES (hex(randomblob(8)), 'humor', '2', '2', 'periodic')"
            )
        db.commit()
        collapse_soul_history(db)
        latest = db.fetchone(
            "SELECT new_value FROM soul_history WHERE soul_id='humor' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1"
        )
        assert latest["new_value"] == "2"  # baseline for drift survives
        db.close()


class TestEventsPrune:
    def test_prune_events_drops_old_keeps_recent(self):
        db = Database(":memory:")
        db.execute(
            "INSERT INTO events (event_type, data, created_at) "
            "VALUES ('decay.run', '{}', datetime('now','-60 days'))"
        )
        db.execute(
            "INSERT INTO events (event_type, data, created_at) "
            "VALUES ('agent.respond', '{}', datetime('now','-1 days'))"
        )
        db.commit()
        deleted = prune_events(db, retention_days=30)
        assert deleted == 1
        remaining = db.fetchall("SELECT event_type FROM events")
        assert [r["event_type"] for r in remaining] == ["agent.respond"]
        db.close()


class TestRunRetention:
    def test_run_retention_reports_counts_and_gates_vacuum(self):
        db = Database(":memory:")
        set_slider(db, "humor", 6)
        snapshot_personality(db)
        # Not enough churn to trigger VACUUM.
        res = run_retention(db)
        assert res["vacuumed"] == 0
        assert res["soul_history_deleted"] == 0
        assert res["events_deleted"] == 0
        db.close()

    def test_run_retention_vacuums_after_large_cleanup(self):
        db = Database(":memory:")
        set_slider(db, "humor", 1)
        # 600 legacy heartbeat dupes → collapse deletes ~599 → over the
        # VACUUM threshold (500).
        for _ in range(600):
            db.execute(
                "INSERT INTO soul_history (id, soul_id, old_value, new_value, changed_by) "
                "VALUES (hex(randomblob(8)), 'humor', '1', '1', 'periodic')"
            )
        db.commit()
        res = run_retention(db)
        assert res["soul_history_deleted"] >= 500
        assert res["vacuumed"] == 1
        db.close()
