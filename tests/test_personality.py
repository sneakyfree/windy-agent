"""Tests for the personality engine.

Tests SOUL.md loading and personality block building with sliders.
"""

from __future__ import annotations

from windyfly.personality.engine import build_personality_block, load_soul


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
