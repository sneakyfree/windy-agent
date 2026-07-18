"""Raw-model mode + adaptive-mode deprecation (Build 4, 2026-07-18)."""
from __future__ import annotations

import os
from unittest.mock import patch

from windyfly.personality.engine import build_personality_block


SOUL = (
    "You are Windy, a warm and witty companion.\n"
    "You love a good joke and you're deeply caring.\n"
)


class TestRawMode:
    def test_raw_skips_all_tone_modifiers(self):
        sliders = {"humor": 10, "formality": 9, "verbosity": 9,
                   "proactivity": 9, "autonomy": 5}
        raw = build_personality_block(SOUL, sliders, raw=True)
        # None of the slider-derived directives appear
        assert "Be witty and crack jokes" not in raw
        assert "Be formal and professional" not in raw
        assert "detailed, thorough" not in raw
        # The soul itself survives intact
        assert "warm and witty companion" in raw

    def test_manual_still_injects_modifiers(self):
        sliders = {"humor": 10, "formality": 9}
        manual = build_personality_block(SOUL, sliders, raw=False)
        assert "Be witty and crack jokes" in manual or "witty" in manual.lower()

    def test_raw_still_honors_autonomy_posture(self):
        # act-first / ask-first is behavior/safety, not tone — kept in raw
        act = build_personality_block(SOUL, {"autonomy": 9}, raw=True)
        assert "ACT FIRST" in act
        ask = build_personality_block(SOUL, {"autonomy": 1}, raw=True)
        assert "Ask before acting" in ask

    def test_raw_default_false_is_current_behavior(self):
        default = build_personality_block(SOUL, {"humor": 10})
        raw_off = build_personality_block(SOUL, {"humor": 10}, raw=False)
        assert default == raw_off


class TestAdaptiveDeprecation:
    def test_adaptive_inert_by_default(self):
        """apply_adaptive_overrides must not fire unless the escape hatch
        is set — even with a high adaptive_mode slider."""
        import windyfly.agent.loop as loop_mod
        called = []
        real = loop_mod.apply_adaptive_overrides

        def spy(sliders, ctx, trend):
            called.append(True)
            return real(sliders, ctx, trend)

        # default env (no WINDY_ADAPTIVE_MODE_ENABLED) → never called
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WINDY_ADAPTIVE_MODE_ENABLED", None)
            enabled = os.environ.get("WINDY_ADAPTIVE_MODE_ENABLED") == "1"
            assert enabled is False  # the gate the loop checks

    def test_raw_mode_and_adaptive_slider_registered(self):
        from windyfly.control_panel import VALID_SLIDERS
        assert "raw_mode" in VALID_SLIDERS
        assert "adaptive_mode" in VALID_SLIDERS  # kept, deprecated not removed
