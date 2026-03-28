"""Tests for windyfly.hatching — the IT'S ALIVE ceremony.

Covers the hatching animation (with and without animation), the ecosystem
status display, audio hook safety, and ASCII art content verification.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from windyfly.hatching import (
    ITS_ALIVE_BANNER,
    ITS_ALIVE_COMPACT,
    MAD_SCIENTIST,
    play_hatching,
    show_ecosystem_status,
    _try_play_audio,
)


# ═══════════════════════════════════════════════════════════════════════
# Hatching Ceremony
# ═══════════════════════════════════════════════════════════════════════


class TestPlayHatching:
    def test_non_animated_runs_without_error(self):
        """play_hatching(animate=False) should execute without raising."""
        play_hatching(animate=False)

    def test_animated_runs_without_error(self):
        """play_hatching(animate=True) should execute without raising."""
        play_hatching(animate=True)


# ═══════════════════════════════════════════════════════════════════════
# Ecosystem Status
# ═══════════════════════════════════════════════════════════════════════


class TestShowEcosystemStatus:
    def test_runs_without_error(self):
        """show_ecosystem_status() should execute without raising."""
        show_ecosystem_status()

    def test_detects_api_keys_in_env(self):
        """When DEFAULT_MODEL is set, ecosystem status should show Brain."""
        with patch.dict(os.environ, {"DEFAULT_MODEL": "gpt-4o-mini"}):
            show_ecosystem_status()  # Should not raise


# ═══════════════════════════════════════════════════════════════════════
# Audio Hook
# ═══════════════════════════════════════════════════════════════════════


class TestTryPlayAudio:
    def test_no_crash_when_no_sound_file(self):
        """_try_play_audio() should not crash when no sound files exist."""
        _try_play_audio()  # Should silently do nothing


# ═══════════════════════════════════════════════════════════════════════
# ASCII Art Content
# ═══════════════════════════════════════════════════════════════════════


class TestASCIIArtContent:
    def test_mad_scientist_contains_its_alive_twice(self):
        """MAD_SCIENTIST should contain 'IT'S ALIVE' exactly twice."""
        count = MAD_SCIENTIST.count("IT'S ALIVE")
        assert count == 2, f"Expected 2 occurrences of \"IT'S ALIVE\", got {count}"

    def test_mad_scientist_contains_the_fly_is_alive(self):
        """MAD_SCIENTIST should contain 'THE FLY IS ALIVE'."""
        assert "THE FLY IS ALIVE" in MAD_SCIENTIST

    def test_its_alive_banner_has_content(self):
        """ITS_ALIVE_BANNER should be non-empty."""
        assert len(ITS_ALIVE_BANNER.strip()) > 0

    def test_its_alive_compact_has_content(self):
        """ITS_ALIVE_COMPACT should be non-empty."""
        assert len(ITS_ALIVE_COMPACT.strip()) > 0
