"""Tests for CLI command help output — verifies all 14 commands are registered.

Uses subprocess to invoke ``windy --help`` (and sub-command help) and
verifies the expected commands, options, and subcommands appear.
"""

from __future__ import annotations

import subprocess
import sys


# ═══════════════════════════════════════════════════════════════════════
# CLI Help Output
# ═══════════════════════════════════════════════════════════════════════


class TestCLIHelp:
    def test_windy_help_shows_all_commands(self):
        """windy --help should list all 14 commands."""
        result = subprocess.run(
            [sys.executable, "-m", "windyfly.cli", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        output = result.stdout
        expected_commands = [
            "go", "init", "setup", "start", "stop", "restart",
            "status", "doctor", "update", "logs", "config", "version",
            "chat", "test",
        ]
        for cmd in expected_commands:
            assert cmd in output, f"Command '{cmd}' not found in --help output"

    def test_windy_go_help(self):
        """windy go --help should show --key, --model, --preset, --no-browser."""
        result = subprocess.run(
            [sys.executable, "-m", "windyfly.cli", "go", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        output = result.stdout
        assert "--key" in output or "-k" in output
        assert "--model" in output or "-m" in output
        assert "--preset" in output or "-p" in output
        assert "--no-browser" in output

    def test_windy_config_help(self):
        """windy config --help should show show, set, reset, path."""
        result = subprocess.run(
            [sys.executable, "-m", "windyfly.cli", "config", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        output = result.stdout
        for sub in ["show", "set", "reset", "path"]:
            assert sub in output, f"Config subcommand '{sub}' not in --help"

    def test_windy_logs_help(self):
        """windy logs --help should show component choices and -f/-n."""
        result = subprocess.run(
            [sys.executable, "-m", "windyfly.cli", "logs", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        output = result.stdout
        assert "brain" in output
        assert "gateway" in output
        assert "-f" in output or "--follow" in output
        assert "-n" in output or "--lines" in output
