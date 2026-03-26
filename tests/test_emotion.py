"""Tests for emotion detector."""

from __future__ import annotations

from windyfly.agent.emotion_detector import detect_emotional_context, get_emotional_trend
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode


class TestEmotionDetection:
    def test_detect_stress_frustrated(self):
        assert detect_emotional_context("Ugh, this is so frustrating") == "stressed"

    def test_detect_stress_caps(self):
        assert detect_emotional_context("THIS IS BROKEN") == "stressed"

    def test_detect_stress_multiple_exclamation(self):
        assert detect_emotional_context("Why won't this work!!!") == "stressed"

    def test_detect_excitement(self):
        assert detect_emotional_context("This is amazing!") == "excited"

    def test_detect_neutral(self):
        assert detect_emotional_context("Can you help me with this?") == "neutral"

    def test_stress_wtf(self):
        assert detect_emotional_context("wtf is going on") == "stressed"

    def test_excitement_perfect(self):
        assert detect_emotional_context("That's perfect, thank you") == "excited"


class TestEmotionalTrend:
    def test_neutral_trend(self):
        db = Database(":memory:")
        for _ in range(5):
            save_episode(db, "user", "Normal message", session_id="s1", emotional_context="neutral")
        trend = get_emotional_trend(db, "s1")
        assert trend == "neutral"
        db.close()

    def test_sustained_stress(self):
        db = Database(":memory:")
        for _ in range(4):
            save_episode(db, "user", "Stressed msg", session_id="s1", emotional_context="stressed")
        trend = get_emotional_trend(db, "s1")
        assert trend == "sustained_stress"
        db.close()

    def test_not_sustained_stress(self):
        db = Database(":memory:")
        save_episode(db, "user", "msg1", session_id="s1", emotional_context="stressed")
        save_episode(db, "user", "msg2", session_id="s1", emotional_context="neutral")
        save_episode(db, "user", "msg3", session_id="s1", emotional_context="stressed")
        trend = get_emotional_trend(db, "s1")
        assert trend == "neutral"  # Not consecutive enough
        db.close()
