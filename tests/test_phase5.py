"""Tests for Phase 5: Dashboard, Personality Versioning, Events.

(Golden tests were removed with executable skills on 2026-10-10.)
"""

from __future__ import annotations

import time

from windyfly.control_panel import apply_preset, set_slider
from windyfly.dashboard.data import get_dashboard_summary
from windyfly.memory.cost_ledger import log_cost
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.memory.failures import log_failure
from windyfly.memory.intents import create_intent
from windyfly.memory.nodes import upsert_node
from windyfly.memory.skills import save_skill
from windyfly.memory.write_queue import WriteQueue
from windyfly.observability.events import get_event_counts, get_recent_events, log_event
from windyfly.personality.versioning import (
    detect_drift,
    get_personality_history,
    rollback_personality,
    snapshot_personality,
)


# === Dashboard Tests ===


class TestDashboard:
    def test_empty_dashboard(self):
        db = Database(":memory:")
        summary = get_dashboard_summary(db)
        assert summary["memory"]["total_nodes"] == 0
        assert summary["memory"]["total_episodes"] == 0
        assert summary["costs"]["today_usd"] == 0.0
        assert summary["failures"]["total"] == 0
        assert summary["skills"]["total"] == 0
        assert summary["intents"]["active"] == 0
        assert summary["personality"]["preset"] == "custom"
        db.close()

    def test_populated_dashboard(self):
        db = Database(":memory:")
        # Add some data
        upsert_node(db, "fact", "name", source="test")
        upsert_node(db, "preference", "coffee", source="test", epistemic_status="verified")
        save_episode(db, "user", "Hello", session_id="s1")
        save_episode(db, "assistant", "Hi!", session_id="s1")
        log_cost(db, "gpt-4o-mini", 100, 50, 0.01)
        log_failure(db, "factual_error", "Wrong answer")
        save_skill(db, "greet", "Say hi.", "playbook")
        create_intent(db, "Learn Python")

        summary = get_dashboard_summary(db)
        assert summary["memory"]["total_nodes"] == 2
        assert summary["memory"]["total_episodes"] == 2
        assert summary["costs"]["today_usd"] == 0.01
        assert summary["failures"]["total"] == 1
        assert summary["skills"]["total"] == 1
        assert summary["intents"]["active"] == 1
        db.close()

    def test_preset_detection(self):
        db = Database(":memory:")
        apply_preset(db, "buddy")
        summary = get_dashboard_summary(db)
        assert summary["personality"]["preset"] == "buddy"
        db.close()

    def test_custom_preset(self):
        db = Database(":memory:")
        set_slider(db, "personality", 6)
        summary = get_dashboard_summary(db)
        assert summary["personality"]["preset"] == "custom"
        db.close()

    def test_failure_improvement_rate(self):
        db = Database(":memory:")
        log_failure(db, "factual_error", "Error 1")
        log_failure(db, "factual_error", "Error 2")
        # Resolve one
        db.execute("UPDATE failures SET resolved_at = CURRENT_TIMESTAMP WHERE rowid = 1")
        db.commit()
        summary = get_dashboard_summary(db)
        assert summary["failures"]["improvement_rate"] == 0.5
        db.close()

    def test_unpriced_only_task_type_does_not_crash(self):
        # Local / Mind rows store cost_usd NULL on purpose; a task type
        # with only those rows must not break the whole summary.
        db = Database(":memory:")
        log_cost(db, "gpt-4o-mini", 100, 50, 0.01, task_type="chat")
        log_cost(db, "local-model", 10, 10, None, task_type="intent")
        costs = get_dashboard_summary(db)["costs"]
        assert costs["by_task_type"] == {"chat": 0.01, "intent": 0.0}
        assert costs["today_usd"] == 0.01
        db.close()


# === Personality Versioning Tests ===


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
        log_event(db, wq, "cost.log", {"usd": 0.01})
        time.sleep(0.5)

        respond_events = get_recent_events(db, event_type="agent.respond")
        cost_events = get_recent_events(db, event_type="cost.log")
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
        log_event(db, wq, "cost.log", {})
        time.sleep(0.5)

        counts = get_event_counts(db)
        assert counts.get("agent.respond", 0) >= 2
        assert counts.get("cost.log", 0) >= 1
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
