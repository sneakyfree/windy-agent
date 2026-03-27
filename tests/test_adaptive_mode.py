"""Tests for adaptive mode — emotion-driven slider overrides."""

from windyfly.personality.engine import apply_adaptive_overrides


class TestAdaptiveOverrides:
    """Test apply_adaptive_overrides."""

    def test_neutral_no_change(self) -> None:
        sliders = {"humor": 7, "warmth": 5, "verbosity": 5}
        result = apply_adaptive_overrides(sliders, "neutral", "neutral")
        assert result is sliders  # Same reference, no copy needed

    def test_stressed_lowers_humor(self) -> None:
        sliders = {"humor": 8, "warmth": 3, "verbosity": 7}
        result = apply_adaptive_overrides(sliders, "stressed", "neutral")
        assert result["humor"] <= 1
        assert result["warmth"] >= 9

    def test_stressed_does_not_mutate_original(self) -> None:
        sliders = {"humor": 8, "warmth": 3, "verbosity": 7}
        original_humor = sliders["humor"]
        apply_adaptive_overrides(sliders, "stressed", "neutral")
        assert sliders["humor"] == original_humor  # Original unchanged

    def test_sustained_stress_full_supportive(self) -> None:
        sliders = {"humor": 7, "warmth": 4, "verbosity": 8, "proactivity": 8}
        result = apply_adaptive_overrides(sliders, "stressed", "sustained_stress")
        assert result["humor"] == 0
        assert result["warmth"] == 10
        assert result["verbosity"] <= 3
        assert result["proactivity"] <= 2

    def test_excited_raises_warmth(self) -> None:
        sliders = {"humor": 5, "warmth": 4}
        result = apply_adaptive_overrides(sliders, "excited", "neutral")
        assert result["warmth"] >= 8
        assert result["humor"] >= 7  # 5 + 2

    def test_excited_humor_capped_at_10(self) -> None:
        sliders = {"humor": 9, "warmth": 5}
        result = apply_adaptive_overrides(sliders, "excited", "neutral")
        assert result["humor"] == 10  # min(9+2, 10) = 10

    def test_excited_trend_overrides(self) -> None:
        sliders = {"humor": 3, "warmth": 3}
        result = apply_adaptive_overrides(sliders, "neutral", "excited")
        assert result["warmth"] >= 8
        assert result["humor"] >= 5  # 3 + 2

    def test_sustained_stress_takes_priority(self) -> None:
        """Sustained stress is more severe than single-message stress."""
        sliders = {"humor": 5, "warmth": 5, "verbosity": 8, "proactivity": 7}
        result = apply_adaptive_overrides(sliders, "stressed", "sustained_stress")
        assert result["humor"] == 0  # Full lockdown
        assert result["verbosity"] <= 3
