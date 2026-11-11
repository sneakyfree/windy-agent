"""Tests for Phase 5: Dashboard, Personality Versioning, Events.

(Golden tests were removed with executable skills on 2026-10-10.)
"""

from __future__ import annotations

import time

from windyfly.control_panel import set_slider
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.observability.events import get_event_counts, get_recent_events, log_event
from windyfly.personality.versioning import (
    detect_drift,
    get_personality_history,
    rollback_personality,
    snapshot_personality,
)


# === Dashboard Tests ===


class TestPersonalityVersioning:
    def test_snapshot(self):
        db = Database(":memory:")
        set_slider(db, "personality", 7)
        batch_id = snapshot_personality(db)
        assert batch_id is not None
        history = get_personality_history(db)
        assert len(history) >= 1
        db.close()

    def test_history_ordered(self):
        db = Database(":memory:")
        set_slider(db, "personality", 5)
        snapshot_personality(db, changed_by="user")
        set_slider(db, "personality", 8)
        snapshot_personality(db, changed_by="user")
        history = get_personality_history(db, limit=5)
        assert len(history) >= 2
        db.close()

    def test_no_drift_when_fresh(self):
        db = Database(":memory:")
        set_slider(db, "personality", 7)
        drift = detect_drift(db)
        # No old snapshots → no drift
        assert drift is None
        db.close()

    def test_rollback_restores_value(self):
        db = Database(":memory:")
        set_slider(db, "personality", 5)
        snapshot_personality(db)

        # Change slider
        set_slider(db, "personality", 9)

        # Rollback to the snapshot (use future date to capture all history)
        restored = rollback_personality(db, "2099-01-01")
        assert restored >= 1
        # The point of a rollback is the VALUE coming back, not a count.
        row = db.fetchone("SELECT value FROM soul WHERE key = 'slider_personality'")
        assert row["value"] == "5"
        db.close()


# === Event Logging Tests ===


class TestEventLogging:
    def test_log_and_retrieve(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        log_event(db, wq, "agent.respond", {"message": "test"})
        time.sleep(0.5)

        events = get_recent_events(db)
        assert len(events) >= 1
        assert events[0]["event_type"] == "agent.respond"
        wq.stop()
        db.close()

    def test_filter_by_type(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        log_event(db, wq, "agent.respond", {"msg": "1"})
        log_event(db, wq, "decay.run", {"usd": 0.01})
        time.sleep(0.5)

        respond_events = get_recent_events(db, event_type="agent.respond")
        cost_events = get_recent_events(db, event_type="decay.run")
        assert len(respond_events) >= 1
        assert len(cost_events) >= 1
        wq.stop()
        db.close()

    def test_event_counts(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        log_event(db, wq, "agent.respond", {})
        log_event(db, wq, "agent.respond", {})
        log_event(db, wq, "decay.run", {})
        time.sleep(0.5)

        counts = get_event_counts(db)
        assert counts.get("agent.respond", 0) >= 2
        assert counts.get("decay.run", 0) >= 1
        wq.stop()
        db.close()

    def test_events_table_exists(self):
        db = Database(":memory:")
        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
        )
        assert len(tables) == 1
        db.close()

    def test_schema_version_is_current(self):
        db = Database(":memory:")
        row = db.fetchone("SELECT MAX(version) as v FROM schema_version")
        # Use >= so future migrations don't keep retroactively breaking
        # this assertion. The point of the test is "schema is migrated,"
        # not "version is exactly N." (Migrations: 7 was Wave 14 tracing
        # spine, 8 added the /goal goals table.)
        assert row["v"] >= 7
        db.close()
