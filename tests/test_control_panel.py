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
        with pytest.raises(ValueError, match="0–10"):
            set_slider(db, "personality", -1)
        db.close()

    def test_value_too_high(self):
        db = Database(":memory:")
        with pytest.raises(ValueError, match="0–10"):
            set_slider(db, "personality", 11)
        db.close()

    def test_defaults_when_empty(self):
        """Dials default to the 5 midpoint; 0/1 toggles default to 0.

        This test used to assert *every* slider defaults to 5, which
        hid a real defect: `raw_mode` is a switch, and `bool(5)` is
        True, so a fresh install ran in raw mode for no stated reason.
        Raw-by-default is now the deliberate policy, declared in
        SLIDER_DEFAULTS — the blanket assertion is replaced by one that
        reads the policy from its single source.
        """
        from windyfly.control_panel import SLIDER_DEFAULTS

        db = Database(":memory:")
        sliders = get_sliders(db)
        for name in VALID_SLIDERS:
            assert sliders[name] == SLIDER_DEFAULTS[name], (
                f"{name} defaulted to {sliders[name]}, "
                f"expected {SLIDER_DEFAULTS[name]}"
            )
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


# ── Toggle sliders must not inherit the dial default ───────────────


def test_every_slider_has_a_declared_default():
    """No slider may fall through to an implicit value.

    `raw_mode` shipped into `_COST_PER_POINT` (hence VALID_SLIDERS)
    with nothing declaring its default, so a 0/1 switch inherited the
    0-10 dials' 5-fill and `bool(5)` turned raw mode ON by accident.
    The behavior happened to match the intent, which is exactly why
    nobody caught it — meanwhile the control panel advertised and
    BILLED for nine tone sliders that reached the model as nothing.

    Any future slider added to the cost model must state its default
    here rather than inherit one.
    """
    from windyfly.control_panel import (
        SLIDER_DEFAULTS,
        TOGGLE_SLIDERS,
        VALID_SLIDERS,
    )

    missing = VALID_SLIDERS - set(SLIDER_DEFAULTS)
    assert not missing, f"sliders with no declared default: {sorted(missing)}"

    # Switches must be 0 or 1 — never a dial value, which `bool()` and
    # `>= 5` gates both read as ON.
    for name in TOGGLE_SLIDERS:
        assert SLIDER_DEFAULTS[name] in (0, 1), (
            f"toggle {name!r} has non-boolean default "
            f"{SLIDER_DEFAULTS[name]}"
        )


def test_default_install_runs_in_raw_mode():
    """Raw is the DEFAULT, deliberately (Grant's call, 2026-07-25).

    A fresh agent runs on the frontier model's native intuition plus
    its soul and memory, with no slider-tuned tone directives injected
    — Principles #3 and #5: don't bolt a hand-tuned tone layer onto a
    model that keeps getting better at reading people than our knobs
    could.

    Asserted end-to-end rather than on the constant, because the thing
    that matters is what reaches the model.
    """
    from windyfly.control_panel import get_sliders
    from windyfly.memory.database import Database
    from windyfly.personality.engine import build_personality_block

    db = Database(":memory:")
    sliders = get_sliders(db)
    assert bool(sliders["raw_mode"]) is True

    soul = "# Soul\nWarm and witty helper."
    block = build_personality_block(
        soul, sliders, raw=bool(sliders["raw_mode"]),
    )
    assert "Behavioral Modifiers" not in block, (
        "raw mode still injected tone directives"
    )
    assert "Warm and witty helper." in block, "raw mode dropped the soul"
    db.close()


def test_turning_raw_off_hands_tone_back_to_the_sliders():
    """The opt-out has to actually work, or the toggle is decoration."""
    from windyfly.control_panel import get_sliders, set_slider
    from windyfly.memory.database import Database
    from windyfly.personality.engine import build_personality_block

    db = Database(":memory:")
    set_slider(db, "raw_mode", 0)
    sliders = get_sliders(db)
    assert bool(sliders["raw_mode"]) is False

    block = build_personality_block(
        "# Soul\nWarm and witty helper.", sliders,
        raw=bool(sliders["raw_mode"]),
    )
    assert "Behavioral Modifiers" in block
    db.close()
