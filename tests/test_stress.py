"""Phase H4 — Stress & Resilience Tests.

Break things under load. Verify the platform handles concurrent requests,
large datasets, malformed inputs, and edge cases without crashing.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import patch

import pytest

from windyfly.bridge.uds_server import UDSBridge
from windyfly.control_panel import PRESETS, VALID_SLIDERS, apply_preset, get_sliders, set_slider
from windyfly.dashboard.data import get_dashboard_summary
from windyfly.memory.cost_ledger import log_cost
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.memory.nodes import upsert_node
from windyfly.memory.write_queue import Priority, WriteQueue


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
# H4.1: 100 Concurrent Slider SET Requests
# =============================================================================


class TestConcurrentLoad:
    def test_100_concurrent_slider_sets(self):
        """H4.1: 100 concurrent slider.set dispatches — no socket errors."""
        bridge, db, _ = _make_bridge()
        errors = []

        async def set_slider_dispatch(i):
            try:
                name = list(VALID_SLIDERS)[i % len(VALID_SLIDERS)]
                value = i % 11
                await bridge._dispatch("sliders.set", {"name": name, "value": value})
            except Exception as e:
                errors.append(f"Request {i}: {e}")

        async def run_all():
            tasks = [set_slider_dispatch(i) for i in range(100)]
            await asyncio.gather(*tasks, return_exceptions=True)

        _run(run_all())
        assert len(errors) == 0, f"Concurrent errors: {errors}"
        db.close()

    def test_50_concurrent_dashboard_gets(self):
        """H4.2: 50 concurrent dashboard.summary dispatches — all valid JSON."""
        bridge, db, _ = _make_bridge()
        results = []

        async def get_dash():
            return await bridge._dispatch("dashboard.summary", {})

        async def run_all():
            tasks = [get_dash() for _ in range(50)]
            return await asyncio.gather(*tasks, return_exceptions=True)

        results = _run(run_all())
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                pytest.fail(f"Dashboard #{i} failed: {r}")
            # Dashboard response may be nested under 'dashboard' key
            data = r.get("dashboard", r)
            assert "memory" in data or "costs" in data, f"Dashboard #{i} missing expected keys"
        db.close()

    def test_100_concurrent_mixed_operations(self):
        """H4.3: Mixed concurrent operations — sliders, search, intents."""
        bridge, db, _ = _make_bridge()
        errors = []

        async def mixed_op(i):
            try:
                op = i % 4
                if op == 0:
                    await bridge._dispatch("sliders.get", {})
                elif op == 1:
                    await bridge._dispatch("memory.search", {"query": f"test{i}", "limit": 5})
                elif op == 2:
                    await bridge._dispatch("intents.list", {})
                else:
                    await bridge._dispatch("cost.daily", {})
            except Exception as e:
                errors.append(f"Op {i}: {e}")

        async def run_all():
            tasks = [mixed_op(i) for i in range(100)]
            await asyncio.gather(*tasks, return_exceptions=True)

        _run(run_all())
        assert len(errors) == 0, f"Mixed concurrency errors: {errors}"
        db.close()


# =============================================================================
# H4.4: Large Dataset Performance
# =============================================================================


class TestLargeDataset:
    def test_search_on_1000_nodes(self):
        """H4.4: Search on 1000+ nodes completes in < 1s."""
        db = Database(":memory:")
        for i in range(1000):
            upsert_node(db, type="fact", name=f"fact_{i}", metadata={"value": f"data_{i}"})

        start = time.time()
        from windyfly.memory.nodes import search_nodes
        results = search_nodes(db, "fact_500", limit=10)
        elapsed = time.time() - start

        assert elapsed < 1.0, f"Search on 1000 nodes took {elapsed:.2f}s (>1s)"
        assert len(results) >= 1
        db.close()

    def test_dashboard_with_5000_rows(self):
        """H4.5: Dashboard aggregation handles 5000 rows in < 3s."""
        db = Database(":memory:")
        for i in range(3000):
            db.execute(
                "INSERT INTO episodes (id, user_id, role, content, session_id) "
                "VALUES (?, 'default', 'user', ?, ?)",
                (f"ep-{i}", f"Message {i}", f"session-{i % 20}"),
            )
        for i in range(1500):
            db.execute(
                "INSERT INTO nodes (id, user_id, type, name, source) "
                "VALUES (?, 'default', 'fact', ?, 'bulk')",
                (f"node-{i}", f"fact_{i}"),
            )
        for i in range(500):
            db.execute(
                "INSERT INTO cost_ledger (id, model, input_tokens, output_tokens, cost_usd) "
                "VALUES (?, 'gpt-4o-mini', 100, 50, 0.01)",
                (f"cost-{i}",),
            )
        db.commit()

        start = time.time()
        summary = get_dashboard_summary(db)
        elapsed = time.time() - start

        assert elapsed < 3.0, f"Dashboard took {elapsed:.2f}s (>3s)"
        assert summary["memory"]["total_nodes"] == 1500
        assert summary["memory"]["total_episodes"] == 3000
        db.close()


# =============================================================================
# H4.6: Decay on Large DB
# =============================================================================


class TestDecayPerformance:
    def test_decay_on_10k_nodes(self):
        """H4.6: Cognitive decay on 10k nodes completes in < 5s."""
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        # Insert 10k nodes (no decay scores set, so none will decay)
        for i in range(10000):
            db.execute(
                "INSERT INTO nodes (id, user_id, type, name, source) "
                "VALUES (?, 'default', 'fact', ?, 'bulk')",
                (f"node-{i}", f"fact_{i}"),
            )
        db.commit()

        start = time.time()
        from windyfly.memory.decay import run_decay
        result = run_decay(db, wq)
        elapsed = time.time() - start

        assert elapsed < 5.0, f"Decay on 10k nodes took {elapsed:.2f}s (>5s)"
        time.sleep(0.5)
        wq.stop()
        db.close()


# =============================================================================
# H4.7–H4.10: Resilience Tests
# =============================================================================


class TestResilience:
    def test_malformed_json_to_dispatch(self):
        """H4.8: Malformed params don't crash the dispatcher."""
        bridge, db, _ = _make_bridge()

        malformed_cases = [
            ("sliders.set", {}),          # Missing required params
            ("sliders.set", {"name": "", "value": 5}),  # Empty name
            ("memory.search", {}),         # Missing query
            ("skills.evaluate", {}),       # Missing skill_id
        ]

        for method, params in malformed_cases:
            try:
                _run(bridge._dispatch(method, params))
            except (ValueError, KeyError, TypeError):
                pass  # Expected — these params are invalid
            except Exception as e:
                # Any other unhandled exception is a bug
                if "Unknown method" not in str(e):
                    pytest.fail(f"Unhandled exception on {method}({params}): {e}")
        db.close()

    def test_empty_body_on_all_post_methods(self):
        """H4.10: Empty params on POST methods → safe default or error."""
        bridge, db, wq = _make_bridge()
        wq.start()

        post_methods = [
            "personality.snapshot",
            "personality.rollback",
            "skills.create",
            "skills.regression",
            "decay.run",
        ]

        for method in post_methods:
            try:
                _run(bridge._dispatch(method, {}))
            except Exception:
                pass  # Error is OK, crash is not
        
        time.sleep(0.5)
        wq.stop()
        db.close()

    def test_rapid_preset_cycling_50x(self):
        """H4.9: 50 rapid preset cycles — no corruption."""
        db = Database(":memory:")
        for _ in range(50):
            for preset_name in PRESETS:
                apply_preset(db, preset_name)
        # Final state should match last preset
        last_preset = list(PRESETS.keys())[-1]
        readback = get_sliders(db)
        for slider, expected in PRESETS[last_preset].items():
            assert readback[slider] == expected
        db.close()


# =============================================================================
# Skills Pipeline Stress
# =============================================================================


class TestSkillsPipelineStress:
    def test_50_skills_create_evaluate(self):
        """H4.5 (skills): 50 skill creates + evaluations — no DB corruption."""
        bridge, db, _ = _make_bridge()
        skill_ids = []

        for i in range(50):
            result = _run(bridge._dispatch("skills.create", {
                "name": f"stress_skill_{i}",
                "code": f"x = {i} + 1",
                "language": "python",
            }))
            skill_ids.append(result["skill_id"])

        # Verify all 50 exist
        list_result = _run(bridge._dispatch("skills.list", {"promoted_only": False}))
        assert len(list_result["skills"]) == 50

        # Evaluate first 10
        for sid in skill_ids[:10]:
            result = _run(bridge._dispatch("skills.evaluate", {"skill_id": sid}))
            assert "evaluation" in result

        db.close()
