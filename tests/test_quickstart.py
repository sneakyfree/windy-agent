"""Tests for windyfly.quickstart — the ``windy go`` zero-friction launcher.

Covers provider detection from key prefixes, config file generation,
signup guides data integrity, and clipboard reading safety.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from windyfly.quickstart import (
    KEY_PATTERNS,
    PROVIDER_MENU,
    SIGNUP_GUIDES,
    detect_provider,
    read_clipboard,
    write_quick_config,
)


# ═══════════════════════════════════════════════════════════════════════
# Provider Detection
# ═══════════════════════════════════════════════════════════════════════


class TestDetectProvider:
    def test_anthropic_key(self):
        """sk-ant- prefix should detect Anthropic."""
        result = detect_provider("sk-ant-api03-abc123xyz")
        assert result is not None
        assert result["provider"] == "Anthropic"
        assert result["env_var"] == "ANTHROPIC_API_KEY"

    def test_openai_key(self):
        """sk- prefix (non-ant) should detect OpenAI."""
        result = detect_provider("sk-proj-abc123xyz456very-long-key")
        assert result is not None
        assert result["provider"] == "OpenAI"
        assert result["env_var"] == "OPENAI_API_KEY"

    def test_xai_key(self):
        """xai- prefix should detect xAI Grok."""
        result = detect_provider("xai-abc123xyz456lengthy-key-here")
        assert result is not None
        assert result["provider"] == "xAI Grok"
        assert result["env_var"] == "GROK_API_KEY"

    def test_gemini_key(self):
        """AIza prefix should detect Google Gemini."""
        result = detect_provider("AIzaSyDabc123xyz456lengthy-key")
        assert result is not None
        assert result["provider"] == "Google Gemini"
        assert result["env_var"] == "GEMINI_API_KEY"

    def test_deepseek_key(self):
        """dsk- prefix should detect DeepSeek."""
        result = detect_provider("dsk-abc123xyz456lengthy-key-here")
        assert result is not None
        assert result["provider"] == "DeepSeek"
        assert result["env_var"] == "DEEPSEEK_API_KEY"

    def test_unknown_format(self):
        """An unrecognized key format should return None."""
        result = detect_provider("completely-unknown-key-format")
        assert result is None

    def test_empty_string(self):
        """Empty string should return None."""
        result = detect_provider("")
        assert result is None

    def test_very_short_string(self):
        """Very short strings should return None (no prefix match)."""
        result = detect_provider("ab")
        assert result is None

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace should be stripped before detection."""
        result = detect_provider("  sk-ant-api03-abc123xyz  ")
        assert result is not None
        assert result["provider"] == "Anthropic"

    def test_sk_prefix_not_confused_with_anthropic(self):
        """sk- (without ant-) should be OpenAI, not Anthropic."""
        # sk-ant- is checked first because it's more specific
        result = detect_provider("sk-abc123xyz")
        assert result is not None
        assert result["provider"] == "OpenAI"


# ═══════════════════════════════════════════════════════════════════════
# Config Writing
# ═══════════════════════════════════════════════════════════════════════


class TestWriteQuickConfig:
    def test_creates_env_file(self, tmp_path: Path, monkeypatch):
        """write_quick_config() should create a .env file."""
        monkeypatch.setattr("windyfly.quickstart.PROJECT_ROOT", tmp_path)
        # Need to also patch the import from setup_wizard
        from windyfly.setup_wizard import PRESETS, PROVIDERS
        monkeypatch.setattr("windyfly.setup_wizard.PROJECT_ROOT", tmp_path)

        write_quick_config("OPENAI_API_KEY", "sk-test123", "gpt-4o-mini")
        env_file = tmp_path / ".env"
        assert env_file.exists()
        content = env_file.read_text(encoding="utf-8")
        assert "OPENAI_API_KEY=sk-test123" in content
        assert "DEFAULT_MODEL=gpt-4o-mini" in content

    def test_creates_toml_file(self, tmp_path: Path, monkeypatch):
        """write_quick_config() should create a windyfly.toml."""
        monkeypatch.setattr("windyfly.quickstart.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("windyfly.setup_wizard.PROJECT_ROOT", tmp_path)

        write_quick_config("OPENAI_API_KEY", "sk-test123", "gpt-4o-mini")
        toml_file = tmp_path / "windyfly.toml"
        assert toml_file.exists()
        content = toml_file.read_text(encoding="utf-8")
        assert 'default_model = "gpt-4o-mini"' in content

    def test_uses_specified_preset(self, tmp_path: Path, monkeypatch):
        """write_quick_config() should use the specified preset."""
        monkeypatch.setattr("windyfly.quickstart.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("windyfly.setup_wizard.PROJECT_ROOT", tmp_path)

        write_quick_config("OPENAI_API_KEY", "sk-test123", "gpt-4o-mini", preset="engineer")
        toml_file = tmp_path / "windyfly.toml"
        content = toml_file.read_text(encoding="utf-8")
        assert 'preset = "engineer"' in content

    def test_handles_all_presets(self, tmp_path: Path, monkeypatch):
        """write_quick_config() should handle all 8 personality presets."""
        from windyfly.setup_wizard import PRESETS
        monkeypatch.setattr("windyfly.quickstart.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("windyfly.setup_wizard.PROJECT_ROOT", tmp_path)

        for preset_name in PRESETS:
            write_quick_config("OPENAI_API_KEY", "sk-test123", "gpt-4o-mini", preset=preset_name)
            toml_file = tmp_path / "windyfly.toml"
            content = toml_file.read_text(encoding="utf-8")
            assert f'preset = "{preset_name}"' in content

    def test_env_file_has_correct_format(self, tmp_path: Path, monkeypatch):
        """The .env file should include all provider key slots."""
        monkeypatch.setattr("windyfly.quickstart.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("windyfly.setup_wizard.PROJECT_ROOT", tmp_path)

        write_quick_config("ANTHROPIC_API_KEY", "sk-ant-test123", "claude-3-5-sonnet-latest")
        content = (tmp_path / ".env").read_text(encoding="utf-8")
        # The configured key should have the value
        assert "ANTHROPIC_API_KEY=sk-ant-test123" in content
        # Other keys should be empty
        assert "OPENAI_API_KEY=" in content
        # Standard env vars should be present
        assert "WINDYFLY_DB_PATH=data/windyfly.db" in content
        assert "LOG_LEVEL=INFO" in content

    def test_creates_data_directory(self, tmp_path: Path, monkeypatch):
        """write_quick_config() should ensure data/ directory exists."""
        monkeypatch.setattr("windyfly.quickstart.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("windyfly.setup_wizard.PROJECT_ROOT", tmp_path)

        write_quick_config("OPENAI_API_KEY", "sk-test123", "gpt-4o-mini")
        assert (tmp_path / "data").exists()


# ═══════════════════════════════════════════════════════════════════════
# Signup Guides
# ═══════════════════════════════════════════════════════════════════════


class TestSignupGuides:
    def test_has_6_entries(self):
        """SIGNUP_GUIDES should have exactly 6 provider entries."""
        assert len(SIGNUP_GUIDES) == 6

    def test_each_guide_has_required_keys(self):
        """Each signup guide should have name, url, steps, env_var, model."""
        for guide in SIGNUP_GUIDES:
            assert "name" in guide, f"Guide missing 'name'"
            assert "url" in guide, f"Guide {guide.get('name', '?')} missing 'url'"
            assert "steps" in guide, f"Guide {guide.get('name', '?')} missing 'steps'"
            assert "env_var" in guide, f"Guide {guide.get('name', '?')} missing 'env_var'"
            assert "model" in guide, f"Guide {guide.get('name', '?')} missing 'model'"
            assert isinstance(guide["steps"], list), f"Guide {guide['name']} steps not a list"
            assert len(guide["steps"]) > 0, f"Guide {guide['name']} has no steps"


# ═══════════════════════════════════════════════════════════════════════
# Clipboard
# ═══════════════════════════════════════════════════════════════════════


class TestReadClipboard:
    def test_returns_string_or_none(self):
        """read_clipboard() should return a string or None, never raise."""
        result = read_clipboard()
        assert result is None or isinstance(result, str)

    def test_does_not_crash_when_no_clipboard_tool(self):
        """read_clipboard() should not crash even if clipboard tools are missing."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = read_clipboard()
            assert result is None or isinstance(result, str)
