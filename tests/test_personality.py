"""Tests for the personality engine.

Tests SOUL.md loading, personality block building with sliders,
and mode overrides.
"""

from __future__ import annotations

from windyfly.personality.engine import build_personality_block, get_mode_override, load_soul
from windyfly.personality.mode import DEFAULT_MODE, VALID_MODES, validate_mode

import pytest


class TestLoadSoul:
    def test_loads_existing_file(self, tmp_path):
        soul_file = tmp_path / "SOUL.md"
        soul_file.write_text("# Test Soul\nBe helpful.")
        text = load_soul(str(soul_file))
        assert "Test Soul" in text
        assert "Be helpful" in text

    def test_returns_default_on_missing(self, tmp_path):
        text = load_soul(str(tmp_path / "nonexistent.md"))
        assert "Windy Fly" in text
        assert len(text) > 10


class TestBuildPersonalityBlock:
    def test_default_sliders(self):
        soul = "# Soul\n- Witty and warm"
        result = build_personality_block(soul, {})
        assert "Witty" in result

    def test_low_humor_strips_witty(self):
        soul = "# Soul\n- Witty and warm\n- Helpful"
        result = build_personality_block(soul, {"humor_level": 2})
        assert "Witty" not in result
        assert "Helpful" in result

    def test_high_formality_adds_instruction(self):
        soul = "# Soul\n- Be helpful"
        result = build_personality_block(soul, {"formality": 8})
        assert "formal" in result.lower()

    def test_low_verbosity_adds_brief(self):
        soul = "# Soul"
        result = build_personality_block(soul, {"verbosity": 2})
        assert "brief" in result.lower()

    def test_high_proactivity_adds_suggestion(self):
        soul = "# Soul"
        result = build_personality_block(soul, {"proactivity": 8})
        assert "suggest" in result.lower() or "anticipate" in result.lower()

    def test_high_reasoning_adds_reasoning(self):
        soul = "# Soul"
        result = build_personality_block(soul, {"reasoning_depth": 8})
        assert "reasoning" in result.lower()


class TestModeOverride:
    def test_companion_returns_none(self):
        assert get_mode_override("companion") is None

    def test_focused_returns_override(self):
        result = get_mode_override("focused")
        assert result is not None
        assert "focused" in result.lower()

    def test_neutral_returns_override(self):
        result = get_mode_override("neutral")
        assert result is not None
        assert "neutral" in result.lower()

    def test_unknown_returns_none(self):
        assert get_mode_override("unknown") is None


class TestModeValidation:
    def test_valid_modes(self):
        assert validate_mode("companion") == "companion"
        assert validate_mode("focused") == "focused"
        assert validate_mode("neutral") == "neutral"

    def test_case_insensitive(self):
        assert validate_mode("FOCUSED") == "focused"
        assert validate_mode("  Neutral  ") == "neutral"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            validate_mode("chaos")

    def test_default_mode(self):
        assert DEFAULT_MODE in VALID_MODES
