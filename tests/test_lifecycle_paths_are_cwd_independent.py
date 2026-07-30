"""Start/stop must work from any directory.

The pid and lock files were referenced eight times as the CWD-RELATIVE
``Path("data/windyfly.pid")``. Start the agent from one directory, run
``/stop`` from another, and the stop path does not find the pid file — it
falls through to the pkill/systemctl fallbacks, and where those don't apply
(a non-systemd install: macOS, or a plain ``windy start``) the user is told
nothing is running while it is.

"I can't turn it off and I can't restart it" is the failure a
non-technical user cannot work around, which puts this squarely under
Principle #8: stability first.

``platform.get_pid_path()` already resolved this correctly and had zero
callers; ``PROJECT_ROOT`` was already computed in ``commands/core.py`` and
not used for it. These tests pin the wiring so it cannot come loose again.
"""

from __future__ import annotations

import os
from pathlib import Path

from windyfly.platform import get_data_dir, get_pid_path, get_project_root


def test_project_root_is_stable_across_chdir(tmp_path, monkeypatch):
    """Everything below rests on this: the root must not move when the
    user cds somewhere else."""
    monkeypatch.delenv("WINDYFLY_HOME", raising=False)
    before = get_project_root()
    monkeypatch.chdir(tmp_path)
    assert get_project_root() == before


def test_pid_path_is_absolute_and_cwd_independent(tmp_path, monkeypatch):
    monkeypatch.delenv("WINDYFLY_HOME", raising=False)
    root = get_project_root()
    before = get_pid_path(root)
    assert before.is_absolute(), f"pid path must be absolute: {before}"
    monkeypatch.chdir(tmp_path)
    assert get_pid_path(get_project_root()) == before


def test_core_module_resolves_lifecycle_files_absolutely():
    """The regression itself: core.py must not hold cwd-relative
    lifecycle paths."""
    from windyfly.commands import core

    assert core._PID_FILE.is_absolute(), core._PID_FILE
    assert core._LOCK_FILE.is_absolute(), core._LOCK_FILE
    assert core._PID_FILE.name == "windyfly.pid"
    assert core._LOCK_FILE.name == "windyfly.lock"


def test_no_cwd_relative_lifecycle_literals_remain():
    """Grep-level guard. Someone adding a ninth
    ``Path("data/windyfly.pid")`` re-creates the bug, and a behavioural
    test would not necessarily catch it — the literal only misbehaves when
    cwd differs, which it usually doesn't in a test run."""
    src = Path(__file__).resolve().parent.parent / "src" / "windyfly"
    offenders = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for marker in ('Path("data/windyfly.pid")', 'Path("data/windyfly.lock")'):
            if marker in text:
                offenders.append(f"{py.relative_to(src)}: {marker}")
    assert not offenders, (
        "cwd-relative lifecycle paths reintroduced — use "
        "platform.get_pid_path(PROJECT_ROOT):\n  " + "\n  ".join(offenders)
    )


def test_honours_windyfly_home_override(tmp_path, monkeypatch):
    """Fleet installs and tests pin the root explicitly; the pid file must
    follow it rather than the cwd."""
    monkeypatch.setenv("WINDYFLY_HOME", str(tmp_path))
    monkeypatch.chdir(Path(os.sep))
    assert get_pid_path(get_project_root()) == tmp_path / "data" / "windyfly.pid"
    assert get_data_dir(get_project_root()) == tmp_path / "data"
