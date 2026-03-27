"""Tier 6 — UX Quality & Polish Audit.

Automated checks that user-facing messages, metadata, error paths,
and branding are raving-fan quality — not generic or broken.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from windyfly.control_panel import (
    PRESETS,
    SLIDER_INFO,
    VALID_SLIDERS,
    estimate_monthly_cost,
    get_slider_info,
    get_sliders,
)
from windyfly.memory.database import Database


PROJECT_ROOT = Path(__file__).parent.parent


# === Budget Message Quality ===


class TestBudgetMessageQuality:
    @patch("windyfly.agent.loop.call_llm")
    @patch("windyfly.agent.loop.check_budget")
    def test_budget_exceeded_message_is_friendly(self, mock_budget, mock_llm):
        """Budget message should include dollar amounts and ask for consent."""
        mock_budget.return_value = {
            "allowed": False,
            "daily_spend": 5.50,
            "daily_budget": 5.0,
            "warning": True,
            "monthly_spend": 10.0,
        }
        from windyfly.agent.loop import agent_respond
        from windyfly.memory.write_queue import WriteQueue

        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        config = {
            "agent": {"default_model": "gpt-4o-mini"},
            "memory": {"max_episodes_per_context": 20, "max_nodes_per_context": 10},
            "personality": {},
            "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
        }

        response = agent_respond(config, db, wq, "Hi", "ux-test-session")

        # Should contain dollar amounts
        assert "$" in response, f"Budget message missing '$': {response}"
        # Should ask for user consent
        assert "?" in response, f"Budget message is not a question: {response}"
        # Should mention budget
        assert "budget" in response.lower(), f"Budget message doesn't say 'budget': {response}"

        wq.stop()
        db.close()


# === Matrix Bot Error Branding ===


class TestMatrixBotBranding:
    def test_error_response_has_fly_emoji(self):
        """The Matrix bot's fallback error message should include 🪰."""
        # Read the source to verify error messages include branding
        bot_file = PROJECT_ROOT / "src" / "windyfly" / "channels" / "matrix_bot.py"
        content = bot_file.read_text()
        # The fallback error message should include the fly emoji
        assert "🪰" in content, "matrix_bot.py error messages missing 🪰 branding"

    def test_offline_error_has_fly_emoji(self):
        """Offline mode error message should include 🪰."""
        with patch("windyfly.agent.offline.is_ollama_available", return_value=False):
            from windyfly.agent.offline import get_offline_response
            result = get_offline_response("Hello!")
            assert "🪰" in result, f"Offline error missing 🪰: {result}"


# === Slider Metadata Completeness ===


class TestSliderMetadata:
    def test_all_sliders_have_info(self):
        """Every valid slider must have complete SLIDER_INFO entry."""
        for name in VALID_SLIDERS:
            assert name in SLIDER_INFO, f"Slider '{name}' missing from SLIDER_INFO"
            info = SLIDER_INFO[name]
            assert info.get("label"), f"Slider '{name}' has no label"
            assert info.get("description"), f"Slider '{name}' has no description"
            assert info.get("impact_low"), f"Slider '{name}' has no impact_low"
            assert info.get("impact_high"), f"Slider '{name}' has no impact_high"

    def test_slider_info_api_returns_complete_data(self):
        """get_slider_info() should return all metadata fields for all sliders."""
        db = Database(":memory:")
        info = get_slider_info(db)
        assert len(info) == 15, f"Expected 15 sliders, got {len(info)}"

        for name, data in info.items():
            assert "label" in data, f"Slider '{name}' missing 'label'"
            assert "description" in data, f"Slider '{name}' missing 'description'"
            assert "impact_low" in data, f"Slider '{name}' missing 'impact_low'"
            assert "impact_high" in data, f"Slider '{name}' missing 'impact_high'"
            assert "value" in data, f"Slider '{name}' missing 'value'"
            assert "cost_per_point" in data, f"Slider '{name}' missing 'cost_per_point'"
            # Value should be a number
            assert isinstance(data["value"], (int, float)), (
                f"Slider '{name}' value is not numeric: {data['value']}"
            )
        db.close()

    def test_slider_descriptions_are_substantive(self):
        """Descriptions should be at least 20 chars (not just 'TODO')."""
        for name in VALID_SLIDERS:
            info = SLIDER_INFO[name]
            assert len(info["description"]) >= 20, (
                f"Slider '{name}' description too short: '{info['description']}'"
            )
            assert len(info["impact_low"]) >= 10, (
                f"Slider '{name}' impact_low too short: '{info['impact_low']}'"
            )
            assert len(info["impact_high"]) >= 10, (
                f"Slider '{name}' impact_high too short: '{info['impact_high']}'"
            )


# === Cost Estimation Realism ===


class TestCostEstimationRealism:
    def test_all_presets_have_positive_cost(self):
        """Every preset except 'silent' should have non-zero cost."""
        for name, values in PRESETS.items():
            cost = estimate_monthly_cost(values)
            if name == "silent":
                # Silent is the cheapest but can still have some cost
                assert cost["estimated_usd"] >= 0
            else:
                assert cost["estimated_usd"] > 0, (
                    f"Preset '{name}' has zero cost estimate"
                )

    def test_powerhouse_most_expensive(self):
        """Powerhouse preset should be the most expensive."""
        costs = {
            name: estimate_monthly_cost(values)["estimated_usd"]
            for name, values in PRESETS.items()
        }
        powerhouse_cost = costs["powerhouse"]
        for name, cost in costs.items():
            if name != "powerhouse":
                assert powerhouse_cost >= cost, (
                    f"Preset '{name}' (${cost}) more expensive than powerhouse (${powerhouse_cost})"
                )

    def test_cost_range_is_reasonable(self):
        """All preset costs should be between $0 and $100/month."""
        for name, values in PRESETS.items():
            cost = estimate_monthly_cost(values)
            assert 0 <= cost["estimated_usd"] <= 100, (
                f"Preset '{name}' cost ${cost['estimated_usd']} is out of reasonable range"
            )


# === SOUL.md Existence ===


class TestSOULFile:
    def test_soul_file_exists(self):
        """SOUL.md referenced in config should exist on disk."""
        soul_path = PROJECT_ROOT / "SOUL.md"
        assert soul_path.exists(), f"SOUL.md not found at {soul_path}"

    def test_soul_file_has_content(self):
        """SOUL.md should not be empty."""
        soul_path = PROJECT_ROOT / "SOUL.md"
        if soul_path.exists():
            content = soul_path.read_text()
            assert len(content) > 100, f"SOUL.md is suspiciously short: {len(content)} chars"


# === CLI Channel Quality ===


class TestCLIQuality:
    def test_cli_welcome_message_has_branding(self):
        """CLI channel should show Windy Fly branding on start."""
        cli_file = PROJECT_ROOT / "src" / "windyfly" / "channels" / "cli.py"
        content = cli_file.read_text()
        assert "🪰" in content, "CLI channel missing 🪰 branding"
        assert "Windy Fly" in content, "CLI channel missing 'Windy Fly' name"

    def test_cli_goodbye_message_exists(self):
        """CLI should have a clean shutdown message."""
        cli_file = PROJECT_ROOT / "src" / "windyfly" / "channels" / "cli.py"
        content = cli_file.read_text()
        assert "Goodbye" in content or "goodbye" in content or "Shutting down" in content, (
            "CLI has no goodbye/shutdown message"
        )


# === Welcome Message Quality ===


class TestWelcomeMessages:
    def test_matrix_welcome_has_personality(self):
        """Matrix bot welcome should include personality and emoji."""
        bot_file = PROJECT_ROOT / "src" / "windyfly" / "channels" / "matrix_bot.py"
        content = bot_file.read_text()
        assert "🪰" in content
        assert "Windy Fly" in content
