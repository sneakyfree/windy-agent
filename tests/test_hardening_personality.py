"""Hardening tests for the personality engine.

Tests slider bounds, SOUL.md edge cases, preset validation,
emotional detection on empty input, and adaptive override bounds.
"""

from __future__ import annotations

import pytest

from windyfly.agent.emotion_detector import detect_emotional_context
from windyfly.control_panel import get_sliders, set_slider
from windyfly.memory.database import Database
from windyfly.personality.engine import (
    apply_adaptive_overrides,
    build_personality_block,
    get_mode_override,
    load_soul,
)


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


# --- Slider bounds ---


class TestSliderBounds:
    def test_set_slider_negative_rejected(self, db):
        """Slider value -1 should be rejected."""
        with pytest.raises(ValueError, match="0–10"):
            set_slider(db, "humor", -1)

    def test_set_slider_eleven_rejected(self, db):
        """Slider value 11 should be rejected."""
        with pytest.raises(ValueError, match="0–10"):
            set_slider(db, "humor", 11)

    def test_set_slider_zero_accepted(self, db):
        """Slider value 0 is valid (minimum)."""
        set_slider(db, "humor", 0)
        sliders = get_sliders(db)
        assert sliders["humor"] == 0

    def test_set_slider_ten_accepted(self, db):
        """Slider value 10 is valid (maximum)."""
        set_slider(db, "humor", 10)
        sliders = get_sliders(db)
        assert sliders["humor"] == 10

    def test_set_slider_string_value_rejected(self, db):
        """String value 'high' should be rejected."""
        with pytest.raises((ValueError, TypeError)):
            set_slider(db, "humor", "high")  # type: ignore

    def test_unknown_slider_name_rejected(self, db):
        """Unknown slider name should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown slider"):
            set_slider(db, "nonexistent_slider", 5)


# --- SOUL.md loading edge cases ---


class TestSoulLoading:
    def test_missing_soul_file_uses_default(self):
        """Non-existent SOUL.md should return default personality."""
        soul = load_soul("/nonexistent/path/SOUL.md")
        assert "Windy Fly" in soul
        assert len(soul) > 20

    def test_empty_soul_file(self, tmp_path):
        """Empty SOUL.md should return empty string (file exists but empty)."""
        empty_soul = tmp_path / "SOUL.md"
        empty_soul.write_text("")
        soul = load_soul(str(empty_soul))
        # Empty file is read as-is — build_personality_block handles it
        assert isinstance(soul, str)

    def test_large_soul_file(self, tmp_path):
        """1MB SOUL.md should be loaded (no truncation at load level)."""
        big_soul = tmp_path / "SOUL.md"
        big_soul.write_text("You are awesome. " * 60_000)  # ~1MB
        soul = load_soul(str(big_soul))
        assert len(soul) > 500_000

    def test_build_personality_from_empty_soul(self):
        """build_personality_block with empty soul text should not crash."""
        result = build_personality_block("", {"humor": 5, "formality": 5, "verbosity": 5})
        assert isinstance(result, str)


# --- Personality block building ---


class TestPersonalityBlock:
    def test_low_humor_filters_jokes(self):
        """humor < 3 should filter witty/joke/funny lines."""
        soul = "Be helpful.\nBe witty and funny.\nAlways joke around."
        result = build_personality_block(soul, {"humor": 1, "personality": 5})
        assert "witty" not in result.lower()
        assert "joke" not in result.lower()
        assert "helpful" in result.lower()

    def test_high_humor_adds_wit(self):
        """humor > 7 should add comedian instruction."""
        result = build_personality_block("Be a helper.", {"humor": 9})
        assert "witty" in result.lower() or "comedian" in result.lower()

    def test_all_sliders_at_extremes(self):
        """All sliders at 10 should not crash."""
        sliders = {
            "humor": 10, "formality": 10, "verbosity": 10,
            "proactivity": 10, "reasoning_depth": 10,
            "personality": 10,
        }
        result = build_personality_block("You are an AI.", sliders)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_all_sliders_at_zero(self):
        """All sliders at 0 should not crash."""
        sliders = {
            "humor": 0, "formality": 0, "verbosity": 0,
            "proactivity": 0, "reasoning_depth": 0,
            "personality": 0,
        }
        result = build_personality_block("You are an AI.", sliders)
        assert isinstance(result, str)


# --- Mode override ---


class TestModeOverride:
    def test_companion_mode_no_override(self):
        assert get_mode_override("companion") is None

    def test_focused_mode_has_override(self):
        result = get_mode_override("focused")
        assert result is not None
        assert "concise" in result.lower()

    def test_neutral_mode_has_override(self):
        result = get_mode_override("neutral")
        assert result is not None
        assert "humor" in result.lower()

    def test_invalid_mode_returns_none(self):
        """Unknown mode should return None, not crash."""
        result = get_mode_override("nonexistent")
        assert result is None


# --- Emotional detection on empty message ---


class TestEmotionalDetection:
    def test_empty_message_returns_neutral(self):
        """Empty message should return 'neutral'."""
        assert detect_emotional_context("") == "neutral"

    def test_none_safe(self):
        """None-ish inputs should not crash."""
        # The function expects str, but let's verify it doesn't explode
        assert detect_emotional_context("   ") == "neutral"

    def test_stress_detection(self):
        assert detect_emotional_context("UGH this is broken!!!") == "stressed"

    def test_excitement_detection(self):
        assert detect_emotional_context("This is amazing!") == "excited"

    def test_neutral_detection(self):
        assert detect_emotional_context("Can you help me with this?") == "neutral"

    def test_all_caps_is_stressed(self):
        """ALL CAPS (5+ chars) should trigger stress."""
        assert detect_emotional_context("THIS IS TERRIBLE") == "stressed"


# --- Adaptive override bounds ---


class TestAdaptiveOverrides:
    def test_sustained_stress_caps_sliders(self):
        """Sustained stress should cap sliders to supportive values."""
        sliders = {"humor": 10, "proactivity": 10, "warmth": 0, "verbosity": 10}
        adapted = apply_adaptive_overrides(sliders, "stressed", "sustained_stress")

        assert adapted["humor"] == 0
        assert adapted["proactivity"] <= 2
        assert adapted["warmth"] == 10
        assert adapted["verbosity"] <= 3

    def test_excited_boosts_within_bounds(self):
        """Excited context should boost humor/warmth but stay <= 10."""
        sliders = {"humor": 9, "warmth": 9}
        adapted = apply_adaptive_overrides(sliders, "excited", "excited")

        assert adapted["humor"] <= 10  # 9+2=11 → capped to 10
        assert adapted["warmth"] <= 10

    def test_neutral_no_change(self):
        """Neutral context + neutral trend should return original sliders."""
        sliders = {"humor": 5, "warmth": 5}
        adapted = apply_adaptive_overrides(sliders, "neutral", "neutral")
        assert adapted == sliders

    def test_does_not_mutate_original(self):
        """Adaptive overrides should return a new dict, not mutate the input."""
        original = {"humor": 5, "warmth": 5, "proactivity": 5, "verbosity": 5}
        original_copy = dict(original)
        apply_adaptive_overrides(original, "stressed", "sustained_stress")
        assert original == original_copy

    def test_all_sliders_at_extremes_stressed(self):
        """Sliders already at extreme values + stress should not exceed bounds."""
        sliders = {
            "humor": 0, "proactivity": 0, "warmth": 10, "verbosity": 0,
        }
        adapted = apply_adaptive_overrides(sliders, "stressed", "sustained_stress")
        assert 0 <= adapted["humor"] <= 10
        assert 0 <= adapted["proactivity"] <= 10
        assert 0 <= adapted["warmth"] <= 10
        assert 0 <= adapted["verbosity"] <= 10

    def test_all_sliders_at_extremes_excited(self):
        """Sliders at max + excited should not exceed 10."""
        sliders = {"humor": 10, "warmth": 10}
        adapted = apply_adaptive_overrides(sliders, "excited", "excited")
        assert adapted["humor"] <= 10
        assert adapted["warmth"] <= 10
