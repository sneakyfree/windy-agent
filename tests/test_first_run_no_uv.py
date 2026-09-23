"""A pip-installed windyfly must run without uv (clean-machine journey, 2026-09-23).

`windy go` printed "✓ uv installed" when the curl|sh installer silently failed
(no curl in slim images, no pipefail), then `windy start` launched `uv` and
crashed with FileNotFoundError.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from windyfly import platform as plat
import windyfly.quickstart as qs


def _pip_layout(tmp_path):
    (tmp_path / ".env").write_text("X=1\n")
    return tmp_path


def _checkout_layout(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='windyfly'\n")
    (tmp_path / "src" / "windyfly").mkdir(parents=True)
    return tmp_path


def test_pip_install_runs_under_current_python(tmp_path):
    assert plat.is_source_checkout(_pip_layout(tmp_path)) is False
    assert plat.python_cmd(tmp_path) == [sys.executable]


def test_checkout_with_uv_uses_uv(tmp_path, monkeypatch):
    root = _checkout_layout(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    assert plat.python_cmd(root) == ["uv", "run", "python"]


def test_checkout_without_uv_falls_back(tmp_path, monkeypatch):
    root = _checkout_layout(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert plat.python_cmd(root) == [sys.executable]


def test_pip_install_needs_no_prereqs(tmp_path, monkeypatch):
    monkeypatch.setattr(qs, "PROJECT_ROOT", _pip_layout(tmp_path))
    monkeypatch.setattr(qs, "can_run", lambda name: False)  # nothing on PATH
    assert qs._needed_prereqs() == []


def test_checkout_still_installs_missing_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(qs, "PROJECT_ROOT", _checkout_layout(tmp_path))
    monkeypatch.setattr(qs, "can_run", lambda name: name == "bun")
    assert qs._needed_prereqs() == ["uv"]


def test_no_false_uv_success(monkeypatch, capsys):
    """The installer "succeeds" but no uv appears: print ✗ and exit, never ✓."""
    monkeypatch.setattr(qs, "IS_WINDOWS", False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(qs, "_tool_available", lambda name: False)
    with pytest.raises(SystemExit):
        qs._install_prereqs(["uv"])
    out = capsys.readouterr().out
    assert "✓" not in out and "Could not install uv" in out


def test_installer_uses_pipefail(monkeypatch):
    seen = []
    monkeypatch.setattr(qs, "IS_WINDOWS", False)
    monkeypatch.setattr(subprocess, "run", lambda args, **k: (seen.append(args), subprocess.CompletedProcess(args, 0))[1])
    monkeypatch.setattr(qs, "_tool_available", lambda name: True)
    qs._install_prereqs(["uv"])
    assert "set -o pipefail" in seen[0][2]


def test_tool_available_finds_installer_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "uv").write_text("#!/bin/sh\n")
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setenv("PATH", "/usr/bin")
    assert qs._tool_available("uv") is True
    assert str(home / ".local" / "bin") in __import__("os").environ["PATH"]
