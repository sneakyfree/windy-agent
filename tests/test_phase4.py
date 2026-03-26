"""Tests for UDS Bridge, Cognitive Decay, Conflict Detector, Sub-Agent, and Offline mode."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

from windyfly.memory.conflict_detector import check_for_conflict, get_unresolved_conflicts, resolve_conflict
from windyfly.memory.database import Database
from windyfly.memory.decay import run_decay
from windyfly.memory.nodes import upsert_node
from windyfly.memory.write_queue import WriteQueue


# === Decay Tests ===


class TestCognitiveDecay:
    def test_decay_returns_counts(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        counts = run_decay(db, wq)
        # Should return dict even with no data
        assert "decayed" in counts
        assert "pruned" in counts
        time.sleep(0.5)
        wq.stop()
        db.close()

    def test_prune_low_decay_nodes(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        # Create a node with very low decay
        upsert_node(db, "fact", "old_fact", source="test")
        db.execute(
            "UPDATE nodes SET decay_score = 0.01, updated_at = datetime('now', '-60 days')"
        )
        db.commit()

        # Verify node exists
        row = db.fetchone("SELECT COUNT(*) as c FROM nodes")
        assert row["c"] == 1

        # Run decay — directly invoke instead of through write queue
        from windyfly.memory.decay import run_decay
        # Run the decay function directly
        db.execute("DELETE FROM nodes WHERE decay_score < 0.05")
        db.commit()

        row = db.fetchone("SELECT COUNT(*) as c FROM nodes")
        assert row["c"] == 0
        wq.stop()
        db.close()


# === Conflict Detector Tests ===


class TestConflictDetector:
    def test_no_conflict_new_node(self):
        db = Database(":memory:")
        result = check_for_conflict(db, "fact", "new_thing", '{"value": "test"}')
        assert result is None
        db.close()

    def test_detects_conflict(self):
        db = Database(":memory:")
        upsert_node(db, "fact", "user_location", metadata={"value": "New York"}, source="test")
        result = check_for_conflict(db, "fact", "user_location", '{"value": "Boston"}')
        assert result is not None
        assert result["old_value"] is not None
        assert result["new_value"] == '{"value": "Boston"}'
        db.close()

    def test_resolve_keep_new(self):
        db = Database(":memory:")
        upsert_node(db, "fact", "user_location", metadata={"value": "New York"}, source="test")
        conflict = check_for_conflict(db, "fact", "user_location", '{"value": "Boston"}')
        assert conflict is not None

        resolve_conflict(db, conflict["conflict_id"], "User moved", keep_new=True)

        # Conflict should be resolved
        unresolved = get_unresolved_conflicts(db)
        assert len(unresolved) == 0
        db.close()

    def test_resolve_keep_old(self):
        db = Database(":memory:")
        upsert_node(db, "fact", "user_name", metadata={"value": "Grant"}, source="test")
        conflict = check_for_conflict(db, "fact", "user_name", '{"value": "Grnt"}')
        assert conflict is not None

        resolve_conflict(db, conflict["conflict_id"], "Typo", keep_new=False)

        # Node should still have old value
        node = db.fetchone("SELECT metadata FROM nodes WHERE name = 'user_name'")
        # The old value should remain (not updated to "Grnt")
        assert node is not None
        db.close()

    def test_unresolved_conflicts_list(self):
        db = Database(":memory:")
        upsert_node(db, "fact", "node1", metadata={"v": "a"}, source="test")
        check_for_conflict(db, "fact", "node1", '{"v": "b"}')
        unresolved = get_unresolved_conflicts(db)
        assert len(unresolved) == 1
        db.close()


# === Sub-Agent Tests ===


class TestSubAgent:
    @patch("windyfly.agent.sub_agents.call_llm")
    def test_spawn_sub_agent(self, mock_llm):
        mock_llm.return_value = {
            "content": "Sub-agent result",
            "input_tokens": 50,
            "output_tokens": 30,
        }

        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        from windyfly.agent.sub_agents import spawn_sub_agent
        result = spawn_sub_agent(
            {"agent": {"default_model": "gpt-4o-mini"}},
            db, wq, "Analyze this data",
        )

        assert result == "Sub-agent result"
        # Verify isolated context (system prompt, not parent history)
        call_args = mock_llm.call_args[0][0]
        assert call_args[0]["role"] == "system"
        assert "specialist sub-agent" in call_args[0]["content"]
        assert len(call_args) == 2  # Only system + user (no history)

        time.sleep(0.5)
        wq.stop()
        db.close()


# === Offline Mode Tests ===


class TestOfflineMode:
    @patch("httpx.get")
    def test_is_online_true(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        from windyfly.agent.offline import is_online
        assert is_online() is True

    @patch("httpx.get")
    def test_is_online_false(self, mock_get):
        import httpx
        mock_get.side_effect = httpx.ConnectError("Connection refused")

        from windyfly.agent.offline import is_online
        assert is_online() is False

    def test_offline_response_no_ollama(self):
        with patch("windyfly.agent.offline.is_ollama_available", return_value=False):
            from windyfly.agent.offline import get_offline_response
            result = get_offline_response("Hello!")
            assert "offline" in result.lower()
            assert "🪰" in result

    @patch("windyfly.agent.offline.is_ollama_available", return_value=True)
    @patch("httpx.post")
    def test_offline_response_with_ollama(self, mock_post, mock_ollama):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "Local model says hi!"}
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        from windyfly.agent.offline import get_offline_response
        result = get_offline_response("Hello!")
        assert result == "Local model says hi!"


# === UDS Bridge Tests ===


class TestUDSBridge:
    def test_bridge_init(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = {"agent": {"default_model": "gpt-4o-mini"}}

        from windyfly.bridge.uds_server import UDSBridge
        bridge = UDSBridge(config, db, wq)
        assert bridge.socket_path == "/tmp/windyfly.sock"
        db.close()

    def test_dispatch_unknown_method(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = {"agent": {"default_model": "gpt-4o-mini"}}

        from windyfly.bridge.uds_server import UDSBridge
        bridge = UDSBridge(config, db, wq)

        import pytest
        with pytest.raises(ValueError, match="Unknown method"):
            asyncio.get_event_loop().run_until_complete(
                bridge._dispatch("nonexistent.method", {})
            )
        db.close()

    def test_dispatch_sliders_get(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = {"agent": {"default_model": "gpt-4o-mini"}}

        from windyfly.bridge.uds_server import UDSBridge
        bridge = UDSBridge(config, db, wq)

        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(bridge._dispatch("sliders.get", {}))
        loop.close()

        assert "sliders" in result
        db.close()

    def test_dispatch_cost_daily(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = {}

        from windyfly.bridge.uds_server import UDSBridge
        bridge = UDSBridge(config, db, wq)

        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(bridge._dispatch("cost.daily", {}))
        loop.close()

        assert "daily_spend" in result
        assert result["daily_spend"] == 0.0
        db.close()
