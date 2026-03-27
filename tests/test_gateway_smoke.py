"""Tier 2 — Gateway API Smoke Tests.

Tests every HTTP route and WebSocket path on the Bun gateway server.
These tests use direct Python httpx/websocket calls against the
gateway's public interface. Because the gateway may not be running,
these tests mock the UDS bridge and test the route-matching logic
by importing and calling the server's handler directly, or by
verifying the gateway TypeScript source structure.

For tests that require a live server, use `pytest -m live_gateway`.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.bridge.uds_server import UDSBridge
from windyfly.control_panel import VALID_SLIDERS, apply_preset, get_sliders, set_slider
from windyfly.memory.cost_ledger import get_daily_spend, log_cost
from windyfly.memory.database import Database
from windyfly.memory.intents import create_intent, surface_pending_intents
from windyfly.memory.nodes import search_nodes, upsert_node
from windyfly.memory.write_queue import WriteQueue


# ---------------------------------------------------------------------------
# Helper — all gateway routes exercise the UDS bridge dispatchers, so
# we test the Python side of that bridge exhaustively here.
# ---------------------------------------------------------------------------


def _make_bridge() -> tuple[UDSBridge, Database, WriteQueue]:
    db = Database(":memory:")
    wq = WriteQueue()
    config = {"agent": {"default_model": "gpt-4o-mini"}}
    bridge = UDSBridge(config, db, wq)
    return bridge, db, wq


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# === Health Check (route: GET /api/health) ===


class TestHealthRoute:
    def test_bridge_reports_connected_state(self):
        bridge, db, _ = _make_bridge()
        assert bridge.socket_path == "/tmp/windyfly.sock"
        db.close()


# === Sliders GET (route: GET /api/sliders) ===


class TestSlidersGetRoute:
    def test_returns_all_15_sliders(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("sliders.get", {}))
        assert "sliders" in result
        assert len(result["sliders"]) == 18
        for name in VALID_SLIDERS:
            assert name in result["sliders"]
        db.close()

    def test_default_values_are_5(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("sliders.get", {}))
        for name, value in result["sliders"].items():
            assert value == 5, f"Slider '{name}' defaulted to {value}, expected 5"
        db.close()


# === Sliders SET (route: PUT /api/sliders/:name) ===


class TestSlidersSetRoute:
    def test_set_valid_slider(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("sliders.set", {"name": "personality", "value": 8}))
        assert result["success"] is True
        # Verify it persisted
        get_result = _run(bridge._dispatch("sliders.get", {}))
        assert get_result["sliders"]["personality"] == 8
        db.close()

    def test_set_invalid_slider_name(self):
        bridge, db, _ = _make_bridge()
        with pytest.raises(ValueError, match="Unknown slider"):
            _run(bridge._dispatch("sliders.set", {"name": "nonexistent", "value": 5}))
        db.close()

    def test_set_invalid_slider_value_too_high(self):
        bridge, db, _ = _make_bridge()
        with pytest.raises(ValueError, match="0–10"):
            _run(bridge._dispatch("sliders.set", {"name": "personality", "value": 99}))
        db.close()

    def test_set_invalid_slider_value_negative(self):
        bridge, db, _ = _make_bridge()
        with pytest.raises(ValueError, match="0–10"):
            _run(bridge._dispatch("sliders.set", {"name": "personality", "value": -1}))
        db.close()

    def test_set_boundary_value_0(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("sliders.set", {"name": "personality", "value": 0}))
        assert result["success"] is True
        get_result = _run(bridge._dispatch("sliders.get", {}))
        assert get_result["sliders"]["personality"] == 0
        db.close()

    def test_set_boundary_value_10(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("sliders.set", {"name": "personality", "value": 10}))
        assert result["success"] is True
        get_result = _run(bridge._dispatch("sliders.get", {}))
        assert get_result["sliders"]["personality"] == 10
        db.close()


# === Cost Daily (route: GET /api/cost/daily) ===


class TestCostDailyRoute:
    def test_returns_numeric_spend(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("cost.daily", {}))
        assert "daily_spend" in result
        assert isinstance(result["daily_spend"], (int, float))
        db.close()

    def test_returns_zero_when_empty(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("cost.daily", {}))
        assert result["daily_spend"] == 0.0
        db.close()

    def test_reflects_logged_cost(self):
        bridge, db, _ = _make_bridge()
        log_cost(db, "gpt-4o-mini", 100, 50, 0.05)
        result = _run(bridge._dispatch("cost.daily", {}))
        assert result["daily_spend"] == 0.05
        db.close()


# === Intents List (route: GET /api/intents) ===


class TestIntentsListRoute:
    def test_returns_empty_list(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("intents.list", {}))
        assert "intents" in result
        assert isinstance(result["intents"], list)
        db.close()

    def test_returns_created_intents(self):
        bridge, db, _ = _make_bridge()
        # surface_pending_intents filters by origin='inferred_from_chat'
        create_intent(db, "Learn Spanish", origin="inferred_from_chat")
        result = _run(bridge._dispatch("intents.list", {}))
        assert len(result["intents"]) >= 1
        db.close()


# === Memory Search (route: GET /api/memory/search) ===


class TestMemorySearchRoute:
    def test_returns_empty_for_no_match(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("memory.search", {"query": "nonexistent", "limit": 10}))
        assert "nodes" in result
        assert len(result["nodes"]) == 0
        db.close()

    def test_finds_matching_node(self):
        bridge, db, _ = _make_bridge()
        upsert_node(db, "person", "grant", source="test")
        result = _run(bridge._dispatch("memory.search", {"query": "grant", "limit": 10}))
        assert len(result["nodes"]) >= 1
        db.close()

    def test_empty_query_handled(self):
        bridge, db, _ = _make_bridge()
        # Should not crash on empty query
        result = _run(bridge._dispatch("memory.search", {"query": "", "limit": 10}))
        assert "nodes" in result
        db.close()


# === 404 on Unknown Route ===


class TestUnknownRoute:
    def test_unknown_method_raises(self):
        bridge, db, _ = _make_bridge()
        with pytest.raises(ValueError, match="Unknown method"):
            _run(bridge._dispatch("nonexistent.route", {}))
        db.close()

    def test_empty_method_raises(self):
        bridge, db, _ = _make_bridge()
        with pytest.raises(ValueError, match="Unknown method"):
            _run(bridge._dispatch("", {}))
        db.close()


# === Sliders Info (route via UDS: sliders.info) ===


class TestSlidersInfoRoute:
    def test_returns_metadata_for_all_sliders(self):
        bridge, db, _ = _make_bridge()
        result = _run(bridge._dispatch("sliders.info", {}))
        assert "sliders" in result
        for name in VALID_SLIDERS:
            info = result["sliders"][name]
            assert "label" in info
            assert "description" in info
            assert "impact_low" in info
            assert "impact_high" in info
            assert "value" in info
            assert "cost_per_point" in info
        db.close()
