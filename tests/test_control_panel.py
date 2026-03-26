"""Tests for the Control Panel and Failure Detector.

Tests presets, slider validation, cost estimation, friction detection,
and friction handling.
"""

from __future__ import annotations

from windyfly.agent.failure_detector import detect_friction, handle_friction
from windyfly.control_panel import (
    PRESETS,
    VALID_SLIDERS,
    apply_preset,
    estimate_monthly_cost,
    get_sliders,
    set_slider,
)
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

import pytest


# === Control Panel Tests ===


class TestPresets:
    def test_apply_buddy(self):
        db = Database(":memory:")
        values = apply_preset(db, "buddy")
        assert values["personality"] == 8
        assert values["proactivity"] == 7
        db.close()

    def test_apply_engineer(self):
        db = Database(":memory:")
        values = apply_preset(db, "engineer")
        assert values["personality"] == 3
        assert values["reasoning_depth"] == 8
        db.close()

    def test_apply_powerhouse(self):
        db = Database(":memory:")
        values = apply_preset(db, "powerhouse")
        assert values["personality"] == 9
        assert values["reasoning_depth"] == 9
        db.close()

    def test_invalid_preset(self):
        db = Database(":memory:")
        with pytest.raises(ValueError, match="Unknown preset"):
            apply_preset(db, "invalid")
        db.close()

    def test_preset_persists(self):
        db = Database(":memory:")
        apply_preset(db, "buddy")
        sliders = get_sliders(db)
        assert sliders["personality"] == 8
        db.close()


class TestSliders:
    def test_set_and_get(self):
        db = Database(":memory:")
        set_slider(db, "personality", 7)
        sliders = get_sliders(db)
        assert sliders["personality"] == 7
        db.close()

    def test_invalid_slider_name(self):
        db = Database(":memory:")
        with pytest.raises(ValueError, match="Unknown slider"):
            set_slider(db, "nonexistent", 5)
        db.close()

    def test_value_too_low(self):
        db = Database(":memory:")
        with pytest.raises(ValueError, match="1–10"):
            set_slider(db, "personality", 0)
        db.close()

    def test_value_too_high(self):
        db = Database(":memory:")
        with pytest.raises(ValueError, match="1–10"):
            set_slider(db, "personality", 11)
        db.close()

    def test_defaults_when_empty(self):
        db = Database(":memory:")
        sliders = get_sliders(db)
        # All should default to 5 when no config
        for name in VALID_SLIDERS:
            assert sliders[name] == 5
        db.close()

    def test_config_defaults(self):
        db = Database(":memory:")
        config = {"personality": 8, "reasoning_depth": 9}
        sliders = get_sliders(db, config_defaults=config)
        assert sliders["personality"] == 8
        assert sliders["reasoning_depth"] == 9
        db.close()


class TestCostEstimation:
    def test_buddy_cost(self):
        values = PRESETS["buddy"]
        cost = estimate_monthly_cost(values)
        assert cost["estimated_usd"] > 0
        assert "personality" in cost["breakdown"]

    def test_powerhouse_more_than_engineer(self):
        eng = estimate_monthly_cost(PRESETS["engineer"])
        pwr = estimate_monthly_cost(PRESETS["powerhouse"])
        assert pwr["estimated_usd"] > eng["estimated_usd"]

    def test_all_zeros(self):
        sliders = {name: 0 for name in VALID_SLIDERS}
        cost = estimate_monthly_cost(sliders)
        assert cost["estimated_usd"] == 0.0


# === Failure Detector Tests ===


class TestFrictionDetection:
    def test_detects_factual_error(self):
        result = detect_friction("No, that's wrong.")
        assert result is not None
        assert result["fault_type"] == "factual_error"

    def test_detects_preference_miss(self):
        result = detect_friction("I told you I prefer dark mode.")
        assert result is not None
        assert result["fault_type"] == "preference_miss"

    def test_detects_execution_failure(self):
        result = detect_friction("Can you try again?")
        assert result is not None
        assert result["fault_type"] == "execution_failure"

    def test_detects_ambiguity(self):
        result = detect_friction("What I meant was something different.")
        assert result is not None
        assert result["fault_type"] == "ambiguity_mishandled"

    def test_no_friction_normal_message(self):
        result = detect_friction("How's the weather today?")
        assert result is None

    def test_includes_context(self):
        result = detect_friction("No, that's wrong.", "Paris is in Germany")
        assert result is not None
        assert result["agent_message"] == "Paris is in Germany"


class TestFrictionHandling:
    def test_logs_and_returns_instruction(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        friction = {
            "fault_type": "factual_error",
            "user_message": "No, that's wrong.",
            "agent_message": "Paris is in Germany",
            "pattern_matched": "test",
        }

        instruction = handle_friction(db, wq, friction)
        assert instruction is not None
        assert "correct" in instruction.lower()

        import time
        time.sleep(0.5)
        wq.stop()
        db.close()

    def test_recurring_gives_extra_warning(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        friction = {
            "fault_type": "factual_error",
            "user_message": "No, that's wrong.",
            "agent_message": "Paris is in Germany",
            "pattern_matched": "test",
        }

        # First occurrence
        from windyfly.memory.failures import log_failure
        log_failure(db, "factual_error", "No, that's wrong.")

        # Same type + description should trigger recurring
        instruction = handle_friction(db, wq, friction)
        assert instruction is not None
        # Should be either a correction or recurring warning
        assert "correct" in instruction.lower() or "recurring" in instruction.lower()

        import time
        time.sleep(0.5)
        wq.stop()
        db.close()
