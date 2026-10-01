"""A pip-installed windyfly must run without uv (clean-machine journey, 2026-09-23).

`windy start` launched `uv` on a pip install and crashed with
FileNotFoundError. (The terminal hatch's uv/bun installer checks went with
the terminal hatch in 0.7.5 — ADR-059, one hallway.)
"""
from __future__ import annotations

import sys

from windyfly import platform as plat


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
