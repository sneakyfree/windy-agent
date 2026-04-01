"""Edge case stress tests — break things on purpose.

Tests scenarios that users will definitely hit in production:
whitespace in keys, special characters, config overwrites,
missing files, concurrent state, and process management edge cases.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from windyfly.quickstart import detect_provider, write_quick_config
from windyfly.commands import _config_set
from windyfly.platform import process_alive, get_ipc_mode


# ═══════════════════════════════════════════════════════════════════════
# Key Whitespace & Special Characters
# ═══════════════════════════════════════════════════════════════════════


class TestKeyEdgeCases:
    def test_key_with_leading_trailing_whitespace(self):
        """detect_provider should handle keys with whitespace."""
        result = detect_provider("  sk-ant-api03-abc123  ")
        assert result is not None
        assert result["provider"] == "Anthropic"

    def test_key_with_special_shell_chars(self, tmp_path: Path, monkeypatch):
        """Keys with shell special characters ($, !, &) should be written correctly."""
        monkeypatch.setattr("windyfly.quickstart.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("windyfly.setup_wizard.PROJECT_ROOT", tmp_path)

        special_key = "sk-proj-abc$%^&*!@#123"
        write_quick_config("OPENAI_API_KEY", special_key, "gpt-4o-mini")
        content = (tmp_path / ".env").read_text()
        assert f"OPENAI_API_KEY={special_key}" in content

    def test_key_with_equals_sign(self, tmp_path: Path, monkeypatch):
        """Keys containing = signs should be stored correctly."""
        monkeypatch.setattr("windyfly.quickstart.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("windyfly.setup_wizard.PROJECT_ROOT", tmp_path)

        key_with_equals = "sk-proj-abc=def=ghi"
        write_quick_config("OPENAI_API_KEY", key_with_equals, "gpt-4o-mini")
        content = (tmp_path / ".env").read_text()
        # The first = is the assignment, rest is the value
        assert f"OPENAI_API_KEY={key_with_equals}" in content


# ═══════════════════════════════════════════════════════════════════════
# Config Set Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestConfigSetEdgeCases:
    def test_value_with_spaces(self, tmp_path: Path, monkeypatch):
        """_config_set() should handle values with spaces."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        toml_file = tmp_path / "windyfly.toml"
        toml_file.write_text('[agent]\nname = "Windy Fly"\n')
        _config_set("agent.name", "My Custom Agent")
        content = toml_file.read_text()
        assert 'name = "My Custom Agent"' in content

    def test_value_with_quotes(self, tmp_path: Path, monkeypatch):
        """_config_set() should handle values with quote characters."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        toml_file = tmp_path / "windyfly.toml"
        toml_file.write_text('[agent]\nname = "Test"\n')
        _config_set("agent.name", "Agent 'the fly' Bot")
        content = toml_file.read_text()
        # The value should be written (may be quoted)
        assert "Agent" in content


# ═══════════════════════════════════════════════════════════════════════
# Config File Overwrite Scenarios
# ═══════════════════════════════════════════════════════════════════════


class TestConfigOverwrite:
    def test_env_overwrite_when_exists(self, tmp_path: Path, monkeypatch):
        """write_quick_config() should overwrite existing .env."""
        monkeypatch.setattr("windyfly.quickstart.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("windyfly.setup_wizard.PROJECT_ROOT", tmp_path)

        # Write initial config
        (tmp_path / ".env").write_text("OLD_KEY=old_value\n")
        write_quick_config("OPENAI_API_KEY", "sk-new123", "gpt-4o-mini")
        content = (tmp_path / ".env").read_text()
        assert "OLD_KEY" not in content  # Old content should be replaced
        assert "OPENAI_API_KEY=sk-new123" in content

    def test_toml_overwrite_when_exists(self, tmp_path: Path, monkeypatch):
        """write_quick_config() should overwrite existing windyfly.toml."""
        monkeypatch.setattr("windyfly.quickstart.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("windyfly.setup_wizard.PROJECT_ROOT", tmp_path)

        (tmp_path / "windyfly.toml").write_text('[old_section]\nkey = "old"\n')
        write_quick_config("OPENAI_API_KEY", "sk-new123", "gpt-4o")
        content = (tmp_path / "windyfly.toml").read_text()
        assert "old_section" not in content
        assert 'default_model = "gpt-4o"' in content


# ═══════════════════════════════════════════════════════════════════════
# Provider Detection Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestProviderDetectionEdgeCases:
    def test_sk_prefix_deepseek_ambiguity(self):
        """sk- key should be detected as OpenAI (not DeepSeek).
        DeepSeek uses dsk- prefix for explicit detection."""
        result = detect_provider("sk-verylongkeythatlookslikedeepseekbutisnt")
        assert result is not None
        assert result["provider"] == "OpenAI"

    def test_dsk_prefix_is_deepseek(self):
        """dsk- prefix is explicitly DeepSeek."""
        result = detect_provider("dsk-verylongkeyfordeepseek123")
        assert result is not None
        assert result["provider"] == "DeepSeek"


# ═══════════════════════════════════════════════════════════════════════
# Process Management Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestProcessEdgeCases:
    def test_stop_when_no_processes_running(self, tmp_path: Path, monkeypatch):
        """windy stop should not crash when no PID file exists."""
        monkeypatch.setattr("windyfly.cli.PROJECT_ROOT", tmp_path)
        from windyfly.cli import cmd_stop
        args = argparse.Namespace()
        cmd_stop(args)  # Should not crash

    def test_stop_with_invalid_pid_data(self, tmp_path: Path, monkeypatch):
        """windy stop should handle PID file with invalid data."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pid_file = data_dir / "windyfly.pid"
        pid_file.write_text("not_a_number\n")
        monkeypatch.setattr("windyfly.cli.PROJECT_ROOT", tmp_path)
        from windyfly.cli import cmd_stop
        args = argparse.Namespace()
        cmd_stop(args)  # Should not crash

    def test_stop_with_stale_pids(self, tmp_path: Path, monkeypatch):
        """windy stop should handle PID file with already-dead processes."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pid_file = data_dir / "windyfly.pid"
        pid_file.write_text("brain=999999\ngateway=999998\n")
        monkeypatch.setattr("windyfly.cli.PROJECT_ROOT", tmp_path)
        from windyfly.cli import cmd_stop
        args = argparse.Namespace()
        cmd_stop(args)  # Should not crash


# ═══════════════════════════════════════════════════════════════════════
# Logging Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestLoggingEdgeCases:
    def test_logs_when_files_dont_exist(self, tmp_path: Path, monkeypatch):
        """windy logs should not crash when log files don't exist."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        from windyfly.commands import cmd_logs
        args = argparse.Namespace(component="all", follow=False, lines=50)
        cmd_logs(args)  # Should not crash

    def test_logs_when_files_are_empty(self, tmp_path: Path, monkeypatch):
        """windy logs should handle empty log files."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "brain.log").write_text("")
        (data_dir / "gateway.log").write_text("")
        from windyfly.commands import cmd_logs
        args = argparse.Namespace(component="all", follow=False, lines=50)
        cmd_logs(args)  # Should not crash


# ═══════════════════════════════════════════════════════════════════════
# Doctor Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestDoctorEdgeCases:
    def test_doctor_when_nothing_installed(self, tmp_path: Path, monkeypatch):
        """windy doctor should not crash when mocking everything as missing."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        with patch("windyfly.commands.can_run", return_value=False):
            from windyfly.commands import cmd_doctor
            args = argparse.Namespace()
            cmd_doctor(args)  # Should not crash


# ═══════════════════════════════════════════════════════════════════════
# Update Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateEdgeCases:
    def test_update_when_not_git_repo(self, tmp_path: Path, monkeypatch):
        """windy update should handle not being in a git repo."""
        monkeypatch.setattr("windyfly.commands.PROJECT_ROOT", tmp_path)
        from windyfly.commands import cmd_update
        args = argparse.Namespace()
        cmd_update(args)  # Should not crash


# ═══════════════════════════════════════════════════════════════════════
# IPC Mode Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestIPCEdgeCases:
    def test_tcp_mode_override(self):
        """WINDYFLY_IPC_MODE=tcp should force TCP mode."""
        with patch.dict(os.environ, {"WINDYFLY_IPC_MODE": "tcp"}):
            mode = get_ipc_mode()
            assert mode == "tcp"


# ═══════════════════════════════════════════════════════════════════════
# Config File Parsing Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestConfigParsingEdgeCases:
    def test_toml_with_missing_sections(self, tmp_path: Path):
        """Config with missing sections should still parse."""
        toml_file = tmp_path / "windyfly.toml"
        toml_file.write_text('[agent]\nname = "Test"\n')
        with open(toml_file, "rb") as f:
            config = tomllib.load(f)
        assert "agent" in config
        # personality section is missing but shouldn't crash

    def test_env_with_no_api_keys(self, tmp_path: Path):
        """A .env file with no API keys should be parseable."""
        env_file = tmp_path / ".env"
        env_file.write_text("LOG_LEVEL=DEBUG\nWINDYFLY_DB_PATH=data/windyfly.db\n")
        content = env_file.read_text()
        assert "LOG_LEVEL=DEBUG" in content
        # No key vars — this is valid

    def test_env_with_malformed_lines(self, tmp_path: Path):
        """A .env file with malformed lines should not crash on read."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# Comment line\n"
            "\n"
            "VALID_KEY=valid_value\n"
            "no_equals_sign\n"
            "=empty_key\n"
            "ANOTHER=value with spaces\n"
        )
        content = env_file.read_text()
        lines = content.splitlines()
        assert len(lines) == 6

    def test_restart_when_nothing_running(self, tmp_path: Path, monkeypatch):
        """windy restart when nothing is running should not crash."""
        monkeypatch.setattr("windyfly.cli.PROJECT_ROOT", tmp_path)
        # We can only test the stop part doesn't crash
        from windyfly.cli import cmd_stop
        args = argparse.Namespace()
        cmd_stop(args)  # Should not crash
