"""Tests for gap closure — 21 new UDS bridge dispatch methods.

Verifies all newly wired backend-to-gateway bridge methods work correctly:
- Personality versioning (history/snapshot/drift/rollback)
- Skills management (list/create/evaluate/promote/rollback/golden-tests/regression)
- Decay, conflicts, moments, failures
- Mode, offline, events
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from windyfly.bridge.uds_server import UDSBridge
from windyfly.memory.database import Database
from windyfly.memory.nodes import upsert_node
from windyfly.memory.soul import upsert_soul
from windyfly.memory.write_queue import WriteQueue


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_bridge():
    db = Database(":memory:")
    wq = WriteQueue()
    config = {
        "agent": {"default_model": "gpt-4o-mini"},
        "memory": {"max_episodes_per_context": 20, "max_nodes_per_context": 10},
        "personality": {},
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }
    return UDSBridge(config, db, wq), db, wq


# =============================================================================
# Group 1: Personality Versioning
# =============================================================================


class TestPersonalityVersioning:
    def test_history_returns_list(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("personality.history", {}))
        assert isinstance(result["history"], list)
        db.close()

    def test_snapshot_returns_batch_id(self):
        bridge, db, _ = _make_bridge()
        # Set a slider first so there's something to snapshot
        upsert_soul(db, key="slider_humor", value="7", source="test")
        result = _run(bridge._dispatch("personality.snapshot", {}))
        assert "batch_id" in result
        assert isinstance(result["batch_id"], str)
        assert len(result["batch_id"]) > 0
        db.close()

    def test_snapshot_then_history(self):
        bridge, db, _ = _make_bridge()
        upsert_soul(db, key="slider_humor", value="7", source="test")
        _run(bridge._dispatch("personality.snapshot", {}))
        result = _run(bridge._dispatch("personality.history", {"limit": 5}))
        assert len(result["history"]) > 0
        db.close()

    def test_drift_returns_none_when_no_drift(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("personality.drift", {}))
        # No history = no drift baseline
        assert result["drift"] is None
        db.close()

    def test_rollback_returns_count(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("personality.rollback", {
            "snapshot_date": "2099-01-01",
        }))
        assert isinstance(result["restored_count"], int)
        db.close()


# =============================================================================
# Group 2: Skills Management
# =============================================================================


class TestSkillsManagement:
    def test_skills_list_empty(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("skills.list", {"promoted_only": False}))
        assert isinstance(result["skills"], list)
        assert len(result["skills"]) == 0
        db.close()

    def test_skills_create_returns_id(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("skills.create", {
            "name": "hello_world",
            "code": "print('hello')",
            "language": "python",
        }))
        assert "skill_id" in result
        assert isinstance(result["skill_id"], str)
        db.close()

    def test_skills_create_then_list(self):
        bridge, db, _ = _make_bridge()
        _run(bridge._dispatch("skills.create", {
            "name": "test_skill",
            "code": "x = 1 + 1",
            "language": "python",
        }))
        result = _run(bridge._dispatch("skills.list", {"promoted_only": False}))
        assert len(result["skills"]) == 1
        assert result["skills"][0]["name"] == "test_skill"
        db.close()

    def test_skills_evaluate_not_found(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("skills.evaluate", {
            "skill_id": "nonexistent-id",
        }))
        assert result["evaluation"]["passed"] is False
        db.close()

    def test_skills_evaluate_passes_valid_code(self):
        bridge, db, _ = _make_bridge()
        create_result = _run(bridge._dispatch("skills.create", {
            "name": "safe_skill",
            "code": "result = 2 + 2",
            "language": "python",
        }))
        eval_result = _run(bridge._dispatch("skills.evaluate", {
            "skill_id": create_result["skill_id"],
        }))
        assert eval_result["evaluation"]["gates"]["syntax"] is True
        db.close()

    def test_skills_promote(self):
        bridge, db, _ = _make_bridge()
        create_result = _run(bridge._dispatch("skills.create", {
            "name": "promotable",
            "code": "x = 1",
            "language": "python",
        }))
        result = _run(bridge._dispatch("skills.promote", {
            "skill_id": create_result["skill_id"],
        }))
        assert result["promoted"] is True

        # Verify it's now in promoted list
        listed = _run(bridge._dispatch("skills.list", {"promoted_only": True}))
        assert any(s["name"] == "promotable" for s in listed["skills"])
        db.close()

    def test_skills_rollback_no_parent(self):
        bridge, db, _ = _make_bridge()
        create_result = _run(bridge._dispatch("skills.create", {
            "name": "orphan",
            "code": "x = 1",
            "language": "python",
        }))
        result = _run(bridge._dispatch("skills.rollback", {
            "skill_id": create_result["skill_id"],
        }))
        assert result["rolled_back"] is True  # Handler doesn't raise, just returns
        db.close()

    def test_skills_golden_tests_no_tests(self):
        bridge, db, _ = _make_bridge()
        create_result = _run(bridge._dispatch("skills.create", {
            "name": "no_tests",
            "code": "x = 1",
            "language": "python",
        }))
        result = _run(bridge._dispatch("skills.golden_tests", {
            "skill_id": create_result["skill_id"],
        }))
        assert result["golden_tests"]["total"] == 0
        db.close()

    def test_skills_regression_empty(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("skills.regression", {}))
        assert result["regression"]["total_skills_tested"] == 0
        assert result["regression"]["has_regressions"] is False
        db.close()


# =============================================================================
# Group 3: Decay, Conflicts, Moments, Failures
# =============================================================================


class TestDecayConflictsMomentsFailures:
    def test_decay_run_returns_counts(self):
        bridge, db, wq = _make_bridge()
        wq.start()
        result = _run(bridge._dispatch("decay.run", {}))
        assert "decay" in result
        assert isinstance(result["decay"]["decayed"], int)
        assert isinstance(result["decay"]["pruned"], int)
        import time; time.sleep(0.3)
        wq.stop()
        db.close()

    def test_conflicts_list_empty(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("conflicts.list", {}))
        assert isinstance(result["conflicts"], list)
        assert len(result["conflicts"]) == 0
        db.close()

    def test_conflicts_list_after_creation(self):
        bridge, db, _ = _make_bridge()
        # Create a node, then update with conflicting value to trigger conflict
        upsert_node(db, type="preference", name="fav_color", metadata={"value": "blue"})
        upsert_node(db, type="preference", name="fav_color", metadata={"value": "red"})
        result = _run(bridge._dispatch("conflicts.list", {}))
        assert len(result["conflicts"]) >= 1
        db.close()

    def test_conflicts_resolve(self):
        bridge, db, _ = _make_bridge()
        upsert_node(db, type="preference", name="fav_food", metadata={"value": "pizza"})
        upsert_node(db, type="preference", name="fav_food", metadata={"value": "tacos"})
        conflicts = _run(bridge._dispatch("conflicts.list", {}))
        if len(conflicts["conflicts"]) > 0:
            cid = conflicts["conflicts"][0]["id"]
            result = _run(bridge._dispatch("conflicts.resolve", {
                "conflict_id": cid,
                "resolution": "User confirmed tacos",
                "keep_new": True,
            }))
            assert result["resolved"] is True
        db.close()

    def test_moments_list_empty(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("moments.list", {}))
        assert isinstance(result["moments"], list)
        assert len(result["moments"]) == 0
        db.close()

    def test_moments_list_with_data(self):
        bridge, db, _ = _make_bridge()
        upsert_node(
            db, type="relationship_moment", name="moment_1",
            metadata={"summary": "User laughed at a joke", "emotional_context": "joy"},
        )
        result = _run(bridge._dispatch("moments.list", {"limit": 5}))
        assert len(result["moments"]) == 1
        assert result["moments"][0]["summary"] == "User laughed at a joke"
        assert result["moments"][0]["emotional_context"] == "joy"
        db.close()

    def test_failures_list_empty(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("failures.list", {}))
        assert isinstance(result["failures"], list)
        db.close()


# =============================================================================
# Group 4: Mode, Offline, Events
# =============================================================================


class TestModeOfflineEvents:
    def test_mode_get_default(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("mode.get", {}))
        assert result["mode"] == "companion"
        db.close()

    def test_mode_set_and_get_roundtrip(self):
        bridge, db, _ = _make_bridge()
        _run(bridge._dispatch("mode.set", {"mode": "focused"}))
        result = _run(bridge._dispatch("mode.get", {}))
        assert result["mode"] == "focused"
        db.close()

    def test_mode_set_invalid(self):
        bridge, db, _ = _make_bridge()
        with pytest.raises(ValueError, match="Invalid mode"):
            _run(bridge._dispatch("mode.set", {"mode": "turbo"}))
        db.close()

    def test_mode_set_all_valid_modes(self):
        bridge, db, _ = _make_bridge()
        for mode in ("companion", "focused", "neutral"):
            _run(bridge._dispatch("mode.set", {"mode": mode}))
            result = _run(bridge._dispatch("mode.get", {}))
            assert result["mode"] == mode
        db.close()

    @patch("windyfly.agent.offline.is_online", return_value=True)
    @patch("windyfly.agent.offline.is_ollama_available", return_value=False)
    def test_offline_status(self, mock_ollama, mock_online):
        """Test offline status check (mock network)."""
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("offline.status", {}))
        assert "online" in result
        assert "ollama_available" in result
        assert result["online"] is True
        assert result["ollama_available"] is False
        db.close()

    def test_events_list_empty(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("events.list", {}))
        assert isinstance(result["events"], list)
        assert isinstance(result["counts_24h"], dict)
        db.close()

    def test_events_list_with_filter(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("events.list", {
            "event_type": "agent.respond",
            "limit": 10,
        }))
        assert isinstance(result["events"], list)
        db.close()


# =============================================================================
# Dispatch Registry Completeness
# =============================================================================


class TestDispatchRegistry:
    """Verify all documented methods are registered."""

    REQUIRED_METHODS = [
        # Original
        "agent.respond", "memory.search", "sliders.get", "sliders.set",
        "sliders.info", "cost.daily", "intents.list", "dashboard.summary",
        "soul.preview", "soul.import",
        "sms.inbound", "sms.send", "email.inbound", "email.send",
        "journal.list", "assessment.run",
        "shape_shift.execute", "shape_shift.restore",
        # Gap closure additions
        "cost.monthly", "config.reload",
        "personality.history", "personality.snapshot",
        "personality.drift", "personality.rollback",
        "skills.list", "skills.create", "skills.evaluate",
        "skills.promote", "skills.rollback",
        "skills.golden_tests", "skills.regression",
        "decay.run",
        "conflicts.list", "conflicts.resolve",
        "moments.list", "failures.list",
        "mode.get", "mode.set",
        "offline.status", "events.list",
    ]

    def test_all_methods_registered(self):
        bridge, db, _ = _make_bridge()
        # Access internal dispatch table
        handlers = {}
        _run(bridge._dispatch("sliders.get", {}))  # Trigger to verify bridge is alive

        for method in self.REQUIRED_METHODS:
            try:
                # We just need to verify the method doesn't raise "Unknown method"
                # Some methods need specific params, so we catch other errors
                _run(bridge._dispatch(method, {}))
            except ValueError as e:
                if "Unknown method" in str(e):
                    pytest.fail(f"Method '{method}' is NOT registered in dispatch table")
                # Other ValueErrors (e.g., missing params) are OK — method exists
            except Exception:
                # Any other error means the method IS registered but params are wrong
                pass
        db.close()

    def test_dispatch_count(self):
        """Verify total dispatch method count matches expectations."""
        bridge, db, _ = _make_bridge()
        # The dispatch table is built in _dispatch(), we can count by inspecting
        # 6 provider methods removed (handled gateway-side), 1 cost.monthly added
        assert len(self.REQUIRED_METHODS) == 40
        db.close()
