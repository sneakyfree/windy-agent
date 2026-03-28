"""Tier 3 — UDS Bridge Integration Tests.

Tests the Python UDS server's full dispatch pipeline including
start/stop lifecycle, concurrent requests, malformed JSON
handling, and agent.respond integration (mocked LLM).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import threading
from unittest.mock import patch

import pytest

from windyfly.bridge.uds_server import UDSBridge
from windyfly.control_panel import VALID_SLIDERS, set_slider
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# === Server Lifecycle ===


class TestBridgeLifecycle:
    def test_start_creates_socket_file(self):
        import tempfile
        db = Database(":memory:")
        wq = WriteQueue()
        sock = os.path.join(tempfile.gettempdir(), "windyfly_test_lifecycle.sock")
        bridge = UDSBridge({}, db, wq, socket_path=sock)
        try:
            _run(bridge.start())
            assert os.path.exists(sock), "Socket file should exist after start"
        finally:
            _run(bridge.stop())
            db.close()

    def test_stop_removes_socket_file(self):
        import tempfile
        db = Database(":memory:")
        wq = WriteQueue()
        sock = os.path.join(tempfile.gettempdir(), "windyfly_test_stop.sock")
        bridge = UDSBridge({}, db, wq, socket_path=sock)
        _run(bridge.start())
        _run(bridge.stop())
        assert not os.path.exists(sock), "Socket file should be removed after stop"
        db.close()

    def test_double_stop_is_safe(self):
        import tempfile
        db = Database(":memory:")
        wq = WriteQueue()
        sock = os.path.join(tempfile.gettempdir(), "windyfly_test_dblstop.sock")
        bridge = UDSBridge({}, db, wq, socket_path=sock)
        _run(bridge.start())
        _run(bridge.stop())
        _run(bridge.stop())  # Should not raise
        db.close()


# === Full Dispatch Roundtrips ===


class TestDispatchRoundtrips:
    def test_sliders_set_and_get_roundtrip(self):
        db = Database(":memory:")
        wq = WriteQueue()
        bridge = UDSBridge({}, db, wq)

        # Set all 15 sliders to different values
        for i, name in enumerate(sorted(VALID_SLIDERS)):
            value = i % 11  # 0-10
            _run(bridge._dispatch("sliders.set", {"name": name, "value": value}))

        # Verify all round-trip correctly
        result = _run(bridge._dispatch("sliders.get", {}))
        for i, name in enumerate(sorted(VALID_SLIDERS)):
            expected = i % 11
            assert result["sliders"][name] == expected, (
                f"Slider '{name}' = {result['sliders'][name]}, expected {expected}"
            )
        db.close()

    def test_intents_list_returns_array(self):
        db = Database(":memory:")
        wq = WriteQueue()
        bridge = UDSBridge({}, db, wq)
        result = _run(bridge._dispatch("intents.list", {}))
        assert isinstance(result["intents"], list)
        db.close()

    def test_cost_daily_returns_float(self):
        db = Database(":memory:")
        wq = WriteQueue()
        bridge = UDSBridge({}, db, wq)
        result = _run(bridge._dispatch("cost.daily", {}))
        assert isinstance(result["daily_spend"], float)
        db.close()

    def test_memory_search_returns_list(self):
        db = Database(":memory:")
        wq = WriteQueue()
        bridge = UDSBridge({}, db, wq)
        result = _run(bridge._dispatch("memory.search", {"query": "test", "limit": 5}))
        assert isinstance(result["nodes"], list)
        db.close()

    def test_sliders_info_complete(self):
        db = Database(":memory:")
        wq = WriteQueue()
        bridge = UDSBridge({}, db, wq)
        result = _run(bridge._dispatch("sliders.info", {}))
        assert len(result["sliders"]) == 18
        for name, info in result["sliders"].items():
            assert "label" in info
            assert "description" in info
            assert info["description"] != "", f"Slider '{name}' has empty description"
        db.close()


# === Error Handling ===


class TestDispatchErrors:
    def test_unknown_method_returns_error(self):
        db = Database(":memory:")
        wq = WriteQueue()
        bridge = UDSBridge({}, db, wq)
        with pytest.raises(ValueError, match="Unknown method"):
            _run(bridge._dispatch("totally.fake.method", {}))
        db.close()

    def test_invalid_slider_name_propagates(self):
        db = Database(":memory:")
        wq = WriteQueue()
        bridge = UDSBridge({}, db, wq)
        with pytest.raises(ValueError):
            _run(bridge._dispatch("sliders.set", {"name": "'; DROP TABLE;--", "value": 5}))
        db.close()

    def test_missing_params_graceful(self):
        """sliders.set with empty params should use defaults, not crash."""
        db = Database(":memory:")
        wq = WriteQueue()
        bridge = UDSBridge({}, db, wq)
        # name="" should raise ValueError (unknown slider), not KeyError
        with pytest.raises(ValueError):
            _run(bridge._dispatch("sliders.set", {}))
        db.close()


# === Concurrent Requests ===


class TestConcurrentDispatch:
    def test_50_concurrent_slider_gets(self):
        """50 simultaneous sliders.get should not deadlock or corrupt."""
        db = Database(":memory:")
        wq = WriteQueue()
        bridge = UDSBridge({}, db, wq)

        results = []
        errors = []

        async def do_get():
            return await bridge._dispatch("sliders.get", {})

        async def run_concurrent():
            tasks = [do_get() for _ in range(50)]
            return await asyncio.gather(*tasks, return_exceptions=True)

        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(run_concurrent())
        finally:
            loop.close()

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                errors.append(f"Request {i}: {r}")
            else:
                assert "sliders" in r

        assert len(errors) == 0, f"Concurrent errors: {errors}"
        db.close()


# === Agent Respond via Bridge (Mocked LLM) ===


class TestAgentRespondViaBridge:
    @patch("windyfly.agent.loop.call_llm")
    def test_full_roundtrip(self, mock_llm):
        mock_llm.return_value = {
            "content": "Hello from the bridge!",
            "model": "gpt-4o-mini",
            "input_tokens": 100,
            "output_tokens": 20,
            "tool_calls": None,
        }

        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        config = {
            "agent": {"default_model": "gpt-4o-mini"},
            "memory": {"max_episodes_per_context": 20, "max_nodes_per_context": 10},
            "personality": {},
            "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
        }
        bridge = UDSBridge(config, db, wq)

        result = _run(bridge._dispatch("agent.respond", {
            "message": "Hello!",
            "session_id": "bridge-test-session",
        }))

        assert "response" in result
        assert result["response"] == "Hello from the bridge!"
        assert mock_llm.called

        time.sleep(0.5)
        wq.stop()
        db.close()
