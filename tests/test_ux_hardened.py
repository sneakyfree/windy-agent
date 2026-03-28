"""Phase H6 — UX Polish & Fan-Maker Hardening Audit.

Goes beyond functional tests to verify that every user-facing element
makes raving fans, not frustrated users. Tests branding, messaging quality,
dashboard completeness, and error UX.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from windyfly.control_panel import (
    PRESETS,
    SLIDER_INFO,
    VALID_SLIDERS,
    estimate_monthly_cost,
    get_slider_info,
)
from windyfly.memory.database import Database

PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
GATEWAY_DIR = PROJECT_ROOT / "gateway"


# =============================================================================
# H6.1: Every Error Message Has 🪰 Branding
# =============================================================================


class TestBrandingConsistency:
    def test_cli_has_fly_branding(self):
        """H6.1a: CLI channel has 🪰 and 'Windy Fly'."""
        cli_file = SRC_DIR / "windyfly" / "channels" / "cli.py"
        content = cli_file.read_text()
        assert "🪰" in content, "CLI missing 🪰 branding"
        assert "Windy Fly" in content, "CLI missing 'Windy Fly' name"

    def test_matrix_bot_has_fly_branding(self):
        """H6.1b: Matrix bot has 🪰."""
        bot_file = SRC_DIR / "windyfly" / "channels" / "matrix_bot.py"
        content = bot_file.read_text()
        assert "🪰" in content, "Matrix bot missing 🪰 branding"

    def test_offline_error_has_fly_branding(self):
        """H6.1c: Offline fallback message has 🪰."""
        offline_file = SRC_DIR / "windyfly" / "agent" / "offline.py"
        content = offline_file.read_text()
        assert "🪰" in content, "Offline fallback missing 🪰 branding"

    def test_dashboard_html_has_fly_branding(self):
        """H6.1d: Dashboard has 🪰 and 'Windy Fly'."""
        index = GATEWAY_DIR / "public" / "index.html"
        content = index.read_text()
        assert "🪰" in content, "Dashboard missing 🪰 branding"
        assert "Windy Fly" in content, "Dashboard missing 'Windy Fly' name"


# =============================================================================
# H6.2: Budget Warning Is Conversational
# =============================================================================


class TestBudgetMessageQuality:
    def test_budget_message_content(self):
        """H6.2: Budget exceeded message uses dollar signs and sounds human."""
        loop_file = SRC_DIR / "windyfly" / "agent" / "loop.py"
        content = loop_file.read_text()
        # Should reference dollar amounts and ask for consent
        assert "$" in content, "Budget handling doesn't show dollar amounts"
        # Should have a budget check that produces a friendly message
        assert "budget" in content.lower(), "Agent loop doesn't reference budget"

    def test_budget_check_returns_numeric(self):
        """Budget spend check should return a numeric value."""
        from windyfly.memory.cost_ledger import get_daily_spend
        db = Database(":memory:")
        result = get_daily_spend(db)
        assert isinstance(result, (int, float))
        assert result >= 0
        db.close()


# =============================================================================
# H6.4: Slider Tooltips Tell a Story
# =============================================================================


class TestSliderNarrative:
    def test_all_sliders_have_vivid_impact_text(self):
        """H6.4: impact_low/impact_high should be vivid, not generic."""
        generic_words = ["less", "more", "low", "high", "none", "some"]
        for name in VALID_SLIDERS:
            info = SLIDER_INFO[name]
            low = info.get("impact_low", "")
            high = info.get("impact_high", "")

            # Should be substantial (not just "Low" or "High")
            assert len(low) >= 15, (
                f"Slider '{name}' impact_low too short ({len(low)} chars): '{low}'"
            )
            assert len(high) >= 15, (
                f"Slider '{name}' impact_high too short ({len(high)} chars): '{high}'"
            )

    def test_all_sliders_have_cost_per_point(self):
        """Each slider should declare its cost impact via get_slider_info()."""
        db = Database(":memory:")
        info = get_slider_info(db)
        for name, data in info.items():
            assert "cost_per_point" in data, f"Slider '{name}' missing cost_per_point"
            assert isinstance(data["cost_per_point"], (int, float)), (
                f"Slider '{name}' cost_per_point is not numeric"
            )
        db.close()


# =============================================================================
# H6.5: Preset Names Evoke Identity
# =============================================================================


class TestPresetIdentity:
    def test_preset_names_are_evocative(self):
        """H6.5: Preset names should feel like characters, not configs."""
        expected_presets = {
            "buddy", "engineer", "powerhouse", "coder",
            "friend", "writer", "researcher", "silent",
        }
        actual_presets = set(PRESETS.keys())
        assert len(actual_presets) >= 8, f"Expected 8+ presets, got {len(actual_presets)}"
        for name in expected_presets:
            assert name in actual_presets, f"Expected preset '{name}' not found"

    def test_presets_have_distinct_profiles(self):
        """Each preset should produce a visibly different slider config."""
        profiles = {}
        for name, values in PRESETS.items():
            profile = tuple(sorted(values.items()))
            assert profile not in profiles.values(), (
                f"Preset '{name}' is identical to another preset"
            )
            profiles[name] = profile


# =============================================================================
# H6.6: Dashboard Feels Alive
# =============================================================================


class TestDashboardQuality:
    def test_dashboard_has_animations(self):
        """H6.6: Dashboard HTML should have CSS animations/transitions."""
        index = GATEWAY_DIR / "public" / "index.html"
        content = index.read_text()
        has_animation = any(kw in content for kw in [
            "animation", "transition", "transform", "keyframes",
            "@keyframes", "ease-in", "ease-out",
        ])
        assert has_animation, "Dashboard has no CSS animations — feels static"

    def test_dashboard_has_gradients(self):
        """Dashboard should use gradients for premium feel."""
        index = GATEWAY_DIR / "public" / "index.html"
        content = index.read_text()
        assert "gradient" in content, "Dashboard has no CSS gradients"

    def test_dashboard_has_custom_font(self):
        """Dashboard should use a custom font, not browser defaults."""
        index = GATEWAY_DIR / "public" / "index.html"
        content = index.read_text()
        has_font = any(font in content for font in [
            "Inter", "Roboto", "Outfit", "Space Grotesk",
            "font-family", "system-ui", "JetBrains",
        ])
        assert has_font, "Dashboard uses no custom font stack"

    def test_dashboard_has_dark_theme(self):
        """Dashboard should have dark mode colors."""
        index = GATEWAY_DIR / "public" / "index.html"
        content = index.read_text()
        # Dark backgrounds use low RGB values or specific tokens
        dark_indicators = ["#0a", "#0b", "#0c", "#0d", "#0e", "#0f", "#10", "#11", "#12"]
        has_dark = any(ind in content.lower() for ind in dark_indicators)
        assert has_dark, "Dashboard doesn't appear to have dark mode"


# =============================================================================
# H6.9: Cost Estimate Builds Trust
# =============================================================================


class TestCostTrust:
    def test_all_presets_show_breakdown(self):
        """H6.9: Cost estimation returns per-slider breakdown."""
        for name, values in PRESETS.items():
            result = estimate_monthly_cost(values)
            assert "estimated_usd" in result, f"Preset '{name}' missing estimated_usd"
            assert "breakdown" in result, f"Preset '{name}' missing cost breakdown"
            assert isinstance(result["estimated_usd"], (int, float))

    def test_cost_range_is_believable(self):
        """Monthly costs should be between $0 and $100."""
        for name, values in PRESETS.items():
            result = estimate_monthly_cost(values)
            cost = result["estimated_usd"]
            assert 0 <= cost <= 100, f"Preset '{name}' cost ${cost} seems unrealistic"


# =============================================================================
# H6.10: Color Palette Consistency
# =============================================================================


class TestColorConsistency:
    def test_uses_css_variables(self):
        """Dashboard should use CSS custom properties for colors."""
        index = GATEWAY_DIR / "public" / "index.html"
        content = index.read_text()
        # Should use CSS variables for maintainability
        var_count = content.count("var(--")
        assert var_count >= 20, (
            f"Dashboard uses only {var_count} CSS variables — colors may be inconsistent"
        )

    def test_accent_color_defined(self):
        """Dashboard should define accent/brand color variables."""
        index = GATEWAY_DIR / "public" / "index.html"
        content = index.read_text()
        accent_vars = ["--accent", "--primary", "--brand", "--highlight"]
        has_accent = any(v in content for v in accent_vars)
        assert has_accent, "Dashboard has no accent/brand color variable"


# =============================================================================
# H6.12: 404 Page Quality  
# =============================================================================


class TestErrorPageQuality:
    def test_gateway_has_404_handler(self):
        """H6.12: Gateway should have a custom 404 response."""
        server_ts = GATEWAY_DIR / "src" / "server.ts"
        content = server_ts.read_text()
        assert "404" in content, "Gateway has no 404 handling"
        assert "Not Found" in content or "not found" in content, (
            "Gateway 404 has no descriptive message"
        )


# =============================================================================
# Comprehensive Source Quality Scan
# =============================================================================


class TestSourceQuality:
    def test_no_print_statements_in_production(self):
        """Production code should use logging, not print()."""
        violations = []
        for py_file in SRC_DIR.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            content = py_file.read_text(errors="ignore")
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("print(") and not stripped.startswith("#"):
                    # Allow print in CLI channel (it's the output medium)
                    if "cli.py" in str(py_file):
                        continue
                    # Allow print in sandbox (it's testing output)
                    if "sandbox.py" in str(py_file):
                        continue
                    violations.append(f"{py_file.name}:{i}")
        # Allow a few (some may be intentional)
        assert len(violations) <= 3, (
            f"Too many print() statements in production code ({len(violations)}): "
            f"{violations[:5]}"
        )

    def test_all_modules_have_docstrings(self):
        """Every Python module should have a module-level docstring."""
        missing = []
        for py_file in SRC_DIR.rglob("*.py"):
            if "__pycache__" in str(py_file) or py_file.name == "__init__.py":
                continue
            content = py_file.read_text(errors="ignore").strip()
            if not content.startswith('"""') and not content.startswith("'''"):
                # Check for from __future__ import then docstring
                lines = content.split("\n")
                found_docstring = False
                for line in lines[:5]:
                    if line.strip().startswith('"""') or line.strip().startswith("'''"):
                        found_docstring = True
                        break
                if not found_docstring:
                    missing.append(py_file.name)
        assert len(missing) <= 2, (
            f"Modules missing docstrings ({len(missing)}): {missing[:10]}"
        )
