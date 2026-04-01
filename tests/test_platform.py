"""Tests for windyfly.platform — cross-platform abstraction layer.

Covers IPC mode detection, IPC path helpers, process management,
path utilities, tool detection, and full diagnostics.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from windyfly.platform import (
    IPCConfig,
    PlatformReport,
    can_run,
    diagnose,
    get_data_dir,
    get_ipc_config,
    get_ipc_mode,
    get_ipc_path,
    get_ipc_tcp_host,
    get_ipc_tcp_port,
    get_log_path,
    get_pid_path,
    get_temp_dir,
    kill_by_name,
    process_alive,
    process_terminate,
)


# ═══════════════════════════════════════════════════════════════════════
# IPC Mode
# ═══════════════════════════════════════════════════════════════════════


class TestGetIPCMode:
    def test_returns_uds_on_posix(self):
        """On Mac/Linux, default IPC mode should be UDS."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WINDYFLY_IPC_MODE", None)
            if sys.platform != "win32":
                assert get_ipc_mode() == "uds"

    def test_respects_env_override_tcp(self):
        """WINDYFLY_IPC_MODE=tcp should override platform default."""
        with patch.dict(os.environ, {"WINDYFLY_IPC_MODE": "tcp"}):
            assert get_ipc_mode() == "tcp"

    def test_respects_env_override_uds(self):
        """WINDYFLY_IPC_MODE=uds should override platform default."""
        with patch.dict(os.environ, {"WINDYFLY_IPC_MODE": "uds"}):
            assert get_ipc_mode() == "uds"

    def test_ignores_invalid_override(self):
        """Invalid WINDYFLY_IPC_MODE should fall back to platform default."""
        with patch.dict(os.environ, {"WINDYFLY_IPC_MODE": "garbage"}):
            mode = get_ipc_mode()
            assert mode in ("uds", "tcp")


# ═══════════════════════════════════════════════════════════════════════
# IPC Path
# ═══════════════════════════════════════════════════════════════════════


class TestGetIPCPath:
    def test_uses_tempdir_not_hardcoded_tmp(self):
        """IPC path should use tempfile.gettempdir(), not hardcoded /tmp."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WINDYFLY_IPC_PATH", None)
            path = get_ipc_path()
            assert path.startswith(tempfile.gettempdir())
            assert path.endswith("windyfly.sock")

    def test_respects_env_override(self):
        """WINDYFLY_IPC_PATH should override the default path."""
        with patch.dict(os.environ, {"WINDYFLY_IPC_PATH": "/custom/path.sock"}):
            assert get_ipc_path() == "/custom/path.sock"


# ═══════════════════════════════════════════════════════════════════════
# IPC TCP helpers
# ═══════════════════════════════════════════════════════════════════════


class TestIPCTCPHelpers:
    def test_default_host(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WINDYFLY_IPC_HOST", None)
            assert get_ipc_tcp_host() == "127.0.0.1"

    def test_default_port(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WINDYFLY_IPC_PORT", None)
            assert get_ipc_tcp_port() == 4001


# ═══════════════════════════════════════════════════════════════════════
# IPC Config
# ═══════════════════════════════════════════════════════════════════════


class TestGetIPCConfig:
    def test_returns_ipc_config_dataclass(self):
        """get_ipc_config() should return an IPCConfig instance."""
        config = get_ipc_config()
        assert isinstance(config, IPCConfig)
        assert config.mode in ("uds", "tcp")
        assert isinstance(config.socket_path, str)
        assert isinstance(config.tcp_host, str)
        assert isinstance(config.tcp_port, int)

    def test_frozen_dataclass(self):
        """IPCConfig should be immutable (frozen)."""
        config = get_ipc_config()
        with pytest.raises(AttributeError):
            config.mode = "tcp"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════
# Process Management
# ═══════════════════════════════════════════════════════════════════════


class TestProcessAlive:
    def test_current_pid_is_alive(self):
        """Current process should report as alive."""
        assert process_alive(os.getpid()) is True

    def test_nonexistent_pid_is_not_alive(self):
        """Absurdly high PID should report as not alive."""
        assert process_alive(999999) is False

    def test_pid_zero(self):
        """PID 0 should be handled without crashing."""
        # On POSIX, kill(0, 0) sends to the process group — might be True
        # The key is it doesn't crash
        result = process_alive(0)
        assert isinstance(result, bool)


class TestProcessTerminate:
    def test_invalid_pid_returns_false(self):
        """Terminating a non-existent PID should return False, not crash."""
        result = process_terminate(999999)
        assert result is False

    def test_returns_bool(self):
        """process_terminate should always return a boolean."""
        result = process_terminate(999999)
        assert isinstance(result, bool)


class TestKillByName:
    def test_nonmatching_pattern_no_crash(self):
        """kill_by_name with a pattern matching nothing should not crash."""
        kill_by_name(["definitely_not_a_real_process_name_xyz_12345"])


# ═══════════════════════════════════════════════════════════════════════
# Path Helpers
# ═══════════════════════════════════════════════════════════════════════


class TestPathHelpers:
    def test_get_data_dir_creates_directory(self, tmp_path: Path):
        """get_data_dir() should create the directory if it doesn't exist."""
        project = tmp_path / "fake_project"
        project.mkdir()
        data_dir = get_data_dir(project)
        assert data_dir.exists()
        assert data_dir.is_dir()
        assert data_dir == project / "data"

    def test_get_data_dir_idempotent(self, tmp_path: Path):
        """Calling get_data_dir twice should not fail."""
        project = tmp_path / "fake_project"
        project.mkdir()
        get_data_dir(project)
        get_data_dir(project)  # Should not raise

    def test_get_log_path_returns_correct_path(self, tmp_path: Path):
        """get_log_path() should return data/<name>.log."""
        project = tmp_path / "fake_project"
        project.mkdir()
        log_path = get_log_path(project, "brain")
        assert log_path == project / "data" / "brain.log"

    def test_get_pid_path_returns_correct_path(self, tmp_path: Path):
        """get_pid_path() should return data/windyfly.pid in project root."""
        project = tmp_path / "fake_project"
        project.mkdir()
        pid_path = get_pid_path(project)
        assert pid_path == project / "data" / "windyfly.pid"

    def test_get_temp_dir_returns_path(self):
        """get_temp_dir() should return a valid Path."""
        result = get_temp_dir()
        assert isinstance(result, Path)
        assert result.exists()


# ═══════════════════════════════════════════════════════════════════════
# Tool Detection
# ═══════════════════════════════════════════════════════════════════════


class TestCanRun:
    def test_python3_available(self):
        """python3 should be available on the test machine."""
        assert can_run("python3") is True

    def test_nonexistent_binary(self):
        """A non-existent binary should return False."""
        assert can_run("nonexistent_binary_xyz_12345") is False


# ═══════════════════════════════════════════════════════════════════════
# Diagnostics
# ═══════════════════════════════════════════════════════════════════════


class TestDiagnose:
    def test_returns_complete_report(self):
        """diagnose() should return a PlatformReport with all fields populated."""
        report = diagnose()
        assert isinstance(report, PlatformReport)
        assert report.system != ""
        assert report.python_version != ""
        assert report.ipc_mode in ("uds", "tcp")
        assert isinstance(report.issues, list)

    def test_detects_correct_python_version(self):
        """diagnose() should report the running Python version."""
        report = diagnose()
        expected = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        assert report.python_version == expected

    def test_flags_issues_when_tools_missing(self):
        """When critical tools are missing, diagnose() should add issues."""
        with patch("windyfly.platform.can_run", return_value=False):
            report = diagnose()
            # Should flag at least uv, bun, git as missing
            issue_text = " ".join(report.issues)
            assert "uv" in issue_text.lower() or len(report.issues) > 0

    def test_no_issues_when_all_tools_present(self):
        """When all tools are present and Python >= 3.12, issues should be about platform only."""
        with patch("windyfly.platform.can_run", return_value=True):
            report = diagnose()
            # On POSIX with Python 3.12+, the only issues might be platform-specific
            # No uv/bun/git issues should be present
            for issue in report.issues:
                assert "uv not found" not in issue
                assert "Bun not found" not in issue
                assert "Git not found" not in issue

    def test_flags_old_python(self):
        """diagnose() should flag Python < 3.12."""
        from collections import namedtuple
        FakeVersion = namedtuple("version_info", ["major", "minor", "micro", "releaselevel", "serial"])
        fake_vi = FakeVersion(3, 11, 0, "final", 0)
        with patch.object(sys, "version_info", fake_vi):
            report = diagnose()
            issue_text = " ".join(report.issues)
            assert "3.12" in issue_text or "Python" in issue_text
