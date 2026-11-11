"""Tests for windyfly.platform — cross-platform abstraction layer.

Covers IPC mode detection, IPC path helpers, process management,
path utilities, tool detection, and full diagnostics.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch


from windyfly.platform import (
    PlatformReport,
    can_run,
    diagnose,
    get_data_dir,
    get_log_path,
    get_pid_path,
    get_temp_dir,
    find_by_name,
    process_alive,
    process_terminate,
)


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


class TestFindByName:
    def test_nonmatching_pattern_finds_nothing(self):
        assert find_by_name(["definitely_not_a_real_process_name_xyz_12345"]) == []

    def test_never_lists_this_process_or_its_parent(self):
        import os
        pids = {pid for pid, _ in find_by_name(["python", "pytest"])}
        assert os.getpid() not in pids and os.getppid() not in pids


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
            # Should flag uv and git as missing. Never Bun: the gateway that needed it is gone (lean Fly R1).
            issue_text = " ".join(report.issues)
            assert "uv not found" in issue_text and "Git not found" in issue_text
            assert "bun" not in issue_text.lower()

    def test_no_issues_when_all_tools_present(self):
        """When all tools are present and Python >= 3.12, issues should be about platform only."""
        with patch("windyfly.platform.can_run", return_value=True):
            report = diagnose()
            # On POSIX with Python 3.12+, the only issues might be platform-specific
            # No uv/git issues should be present
            for issue in report.issues:
                assert "uv not found" not in issue
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
