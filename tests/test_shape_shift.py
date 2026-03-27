"""Tests for shape-shift engine."""

from windyfly.agent.shape_shift import (
    get_shift_announcement,
    shape_shift,
)
from windyfly.control_panel import get_sliders, apply_preset, PRESETS
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


class TestShapeShift:
    def test_saves_and_restores_sliders(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        # Set known starting point
        apply_preset(db, "buddy")
        original = get_sliders(db)

        # Shape-shift to coder
        with shape_shift(db, wq, "coder") as shifted:
            current = get_sliders(db)
            assert current["personality"] == PRESETS["coder"]["personality"]
            assert current["humor"] == PRESETS["coder"]["humor"]

        # After exit, sliders are restored
        restored = get_sliders(db)
        for key in original:
            assert restored[key] == original[key], f"{key}: {restored[key]} != {original[key]}"

        wq.stop()
        db.close()

    def test_custom_slider_overrides(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        apply_preset(db, "buddy")
        original = get_sliders(db)

        with shape_shift(db, wq, {"humor": 0, "warmth": 0, "verbosity": 1}):
            current = get_sliders(db)
            assert current["humor"] == 0
            assert current["warmth"] == 0
            assert current["verbosity"] == 1

        restored = get_sliders(db)
        assert restored["humor"] == original["humor"]

        wq.stop()
        db.close()

    def test_restores_on_exception(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        apply_preset(db, "buddy")
        original = get_sliders(db)

        try:
            with shape_shift(db, wq, "coder"):
                raise RuntimeError("Task failed")
        except RuntimeError:
            pass

        restored = get_sliders(db)
        for key in original:
            assert restored[key] == original[key]

        wq.stop()
        db.close()


class TestAutonomyGating:
    def test_low_autonomy_asks_permission(self):
        msg = get_shift_announcement(2, "coder")
        assert msg is not None
        assert "Which do you prefer?" in msg

    def test_mid_autonomy_announces(self):
        msg = get_shift_announcement(5, "researcher")
        assert msg is not None
        assert "Switching to" in msg
        assert "Which do you prefer?" not in msg

    def test_high_autonomy_silent(self):
        msg = get_shift_announcement(8, "coder")
        assert msg is None

    def test_boundary_values(self):
        assert get_shift_announcement(3, "x") is not None  # asks
        assert "Which do you prefer?" in get_shift_announcement(3, "x")
        assert get_shift_announcement(4, "x") is not None  # announces
        assert "Which do you prefer?" not in get_shift_announcement(4, "x")
        assert get_shift_announcement(7, "x") is None  # silent


class TestToolCoexistence:
    def test_both_tools_registered(self):
        from windyfly.tools.registry import ToolRegistry
        from windyfly.agent.sub_agents import register_sub_agent_tool

        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        config = {"agent": {"default_model": "gpt-4o-mini"}, "costs": {"daily_budget_usd": 5.0}}
        registry = ToolRegistry()
        register_sub_agent_tool(registry, config, db, wq)

        # Both should be registered
        tool_names = [t["function"]["name"] for t in registry.get_schemas()]
        assert "delegate_to_specialist" in tool_names
        assert "shape_shift" in tool_names
        assert "shape_shift_restore" in tool_names

        wq.stop()
        db.close()
