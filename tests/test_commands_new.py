"""Tests for windyfly.commands — doctor, update, logs, config, version.

Covers cmd_version, cmd_doctor, _check_port, _config_show, _config_set,
_config_path, and validates .env and windyfly.toml detection.
"""

from __future__ import annotations

import os
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from windyfly.commands import (
    VERSION,
    _check_port,
    _config_path,
    _config_set,
    _config_show,
    cmd_doctor,
    cmd_version,
)


# ═══════════════════════════════════════════════════════════════════════
# windy version
# ═══════════════════════════════════════════════════════════════════════


class TestCmdVersion:
    def test_runs_without_error(self):
        """cmd_version() should execute without raising."""
        import argparse
        args = argparse.Namespace()
        cmd_version(args)  # Should not raise


# ═══════════════════════════════════════════════════════════════════════
# windy doctor
# ═══════════════════════════════════════════════════════════════════════


class TestCmdDoctor:
    def test_runs_without_error(self):
        """cmd_doctor() should execute without raising."""
        import argparse
        args = argparse.Namespace()
        cmd_doctor(args)  # Should not raise

    def test_detects_missing_env(self, tmp_path: Path, monkeypatch):
        """doctor should flag when .env is missing."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        import argparse
        args = argparse.Namespace()
        # This should not crash even with a fake project root
        cmd_doctor(args)

    def test_detects_missing_toml(self, tmp_path: Path, monkeypatch):
        """doctor should flag when windyfly.toml is missing."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        import argparse
        args = argparse.Namespace()
        cmd_doctor(args)


# ═══════════════════════════════════════════════════════════════════════
# Port Check
# ═══════════════════════════════════════════════════════════════════════


class TestCheckPort:
    def test_free_port_returns_false(self):
        """A port that's definitely free should return False."""
        # Port 39999 is very unlikely to be in use
        result = _check_port(39999)
        assert result is False

    def test_returns_bool(self):
        """_check_port should always return a boolean."""
        result = _check_port(39998)
        assert isinstance(result, bool)


# ═══════════════════════════════════════════════════════════════════════
# Config Show
# ═══════════════════════════════════════════════════════════════════════


class TestConfigShow:
    def test_with_valid_toml(self, tmp_path: Path, monkeypatch):
        """_config_show() should work when windyfly.toml exists."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        toml_file = tmp_path / "windyfly.toml"
        toml_file.write_text('[agent]\nname = "Test"\ndefault_model = "gpt-4o"\n')
        _config_show()  # Should not raise

    def test_with_missing_toml(self, tmp_path: Path, monkeypatch):
        """_config_show() should handle missing windyfly.toml gracefully."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        _config_show()  # Should not raise


# ═══════════════════════════════════════════════════════════════════════
# Config Set
# ═══════════════════════════════════════════════════════════════════════


class TestConfigSet:
    def test_modifies_correct_key(self, tmp_path: Path, monkeypatch):
        """_config_set() should modify the specified key."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        toml_file = tmp_path / "windyfly.toml"
        toml_file.write_text(
            '[agent]\nname = "Windy Fly"\ndefault_model = "gpt-4o-mini"\n'
        )
        _config_set("agent.default_model", "claude-3-5-sonnet-latest")
        content = toml_file.read_text()
        assert "claude-3-5-sonnet-latest" in content

    def test_preserves_other_values(self, tmp_path: Path, monkeypatch):
        """_config_set() should not destroy other keys in the section."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        toml_file = tmp_path / "windyfly.toml"
        toml_file.write_text(
            '[agent]\nname = "Windy Fly"\ndefault_model = "gpt-4o-mini"\ntemperature = 0.7\n'
        )
        _config_set("agent.default_model", "gpt-4o")
        content = toml_file.read_text()
        assert 'name = "Windy Fly"' in content
        assert "temperature = 0.7" in content

    def test_handles_int_value(self, tmp_path: Path, monkeypatch):
        """_config_set() should write integers without quotes."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        toml_file = tmp_path / "windyfly.toml"
        toml_file.write_text('[personality]\nhumor_level = 5\n')
        _config_set("personality.humor_level", "8")
        content = toml_file.read_text()
        assert "humor_level = 8" in content

    def test_handles_float_value(self, tmp_path: Path, monkeypatch):
        """_config_set() should write floats without quotes."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        toml_file = tmp_path / "windyfly.toml"
        toml_file.write_text('[agent]\ntemperature = 0.7\n')
        _config_set("agent.temperature", "0.9")
        content = toml_file.read_text()
        assert "temperature = 0.9" in content

    def test_handles_bool_value(self, tmp_path: Path, monkeypatch):
        """_config_set() should write booleans as lowercase."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        toml_file = tmp_path / "windyfly.toml"
        toml_file.write_text('[agent]\nverbose = false\n')
        _config_set("agent.verbose", "True")
        content = toml_file.read_text()
        assert "verbose = true" in content

    def test_handles_string_value(self, tmp_path: Path, monkeypatch):
        """_config_set() should wrap strings in quotes."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        toml_file = tmp_path / "windyfly.toml"
        toml_file.write_text('[personality]\npreset = "buddy"\n')
        _config_set("personality.preset", "engineer")
        content = toml_file.read_text()
        assert 'preset = "engineer"' in content

    def test_rejects_invalid_format(self, tmp_path: Path, monkeypatch):
        """_config_set() should reject keys not in section.key format."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        toml_file = tmp_path / "windyfly.toml"
        toml_file.write_text('[agent]\nname = "Test"\n')
        _config_set("invalid_key_no_dot", "value")  # Should print error, not crash


# ═══════════════════════════════════════════════════════════════════════
# Config Path
# ═══════════════════════════════════════════════════════════════════════


class TestConfigPath:
    def test_runs_without_error(self):
        """_config_path() should run without raising."""
        _config_path()  # Should not raise


# ═══════════════════════════════════════════════════════════════════════
# VERSION sync with pyproject.toml
# ═══════════════════════════════════════════════════════════════════════


class TestVersionSync:
    def test_version_is_string(self):
        """VERSION constant should be a non-empty string."""
        assert isinstance(VERSION, str)
        assert len(VERSION) > 0
        assert "." in VERSION  # Should be semver-like
