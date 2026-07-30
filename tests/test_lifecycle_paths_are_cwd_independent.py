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

import pytest

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


# ``providers.py`` holds the ONE deliberate cwd-relative literal in the
# tree: ``_LEGACY_OVERRIDES_PATH`` is the old location a running install
# is migrated *away* from, so it must keep resolving the old way or the
# self-heal reads the wrong file. The note above it records an earlier
# "fix" that re-imported the contamination it was removing. Allowed by
# name, so the guard below stays a guard rather than being switched off.
_DELIBERATE_CWD_RELATIVE = {
    "agent/providers.py": {'Path("data/providers.json")'},
}


def test_no_cwd_relative_state_literals_remain():
    """The same bug class, widened past the pid/lock pair.

    Every one of these was found by grep, not by a failing test, because
    a cwd-relative literal behaves perfectly until the user runs from
    somewhere else — and then fails *quietly*: /soul says SOUL.md does
    not exist, /backup writes an empty archive, /config set scatters API
    keys into whatever folder they were standing in and forgets them on
    restart. Silent wrong answers are the honey-badger failure mode that
    Principle #8 puts first.
    """
    src = Path(__file__).resolve().parent.parent / "src" / "windyfly"
    markers = (
        'Path("data/',
        'Path(".env")',
        'Path("SOUL.md")',
        'Path("windyfly.toml")',
    )
    offenders = []
    for py in src.rglob("*.py"):
        rel = str(py.relative_to(src))
        allowed = _DELIBERATE_CWD_RELATIVE.get(rel, set())
        for lineno, line in enumerate(
            py.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # prose about the bug, not the bug
            if any(literal in line for literal in allowed):
                continue
            for marker in markers:
                if marker in line:
                    offenders.append(f"{rel}:{lineno}: {stripped[:70]}")
    assert not offenders, (
        "cwd-relative state paths — anchor to PROJECT_ROOT (see "
        "commands/core.py) so they survive the user cd'ing:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.asyncio
async def test_export_from_another_directory_is_not_empty(tmp_path, monkeypatch):
    """The failure this prevents is a backup that lies.

    Run from anywhere but the install directory, /export matched nothing
    and reported "Backup saved: ... (0 files)" — a real tarball, created
    successfully, containing none of the user's data. Nobody finds out
    until they try to restore.
    """
    import tarfile

    from windyfly.commands import core as core_mod
    from windyfly.commands.registry import registry

    install = tmp_path / "install"
    (install / "data" / "sounds").mkdir(parents=True)
    (install / "SOUL.md").write_text("i am the soul", encoding="utf-8")
    (install / "data" / "windyfly.db").write_text("db", encoding="utf-8")
    (install / "data" / "sounds" / "its-alive.wav").write_text("wav", encoding="utf-8")

    monkeypatch.setattr(core_mod, "PROJECT_ROOT", install)
    registry._commands.clear()
    registry._aliases.clear()
    core_mod._register_all()

    # The whole point: stand somewhere else entirely.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    # The tar lives on /export; /backup is the separate cloud command
    # and shadows export's alias of the same name.
    result = await registry.get("export").handler({})
    assert "Backup failed" not in result, result
    assert "(0 files)" not in result, f"backup was empty: {result}"

    archives = list(elsewhere.glob("windyfly-backup-*.tar.gz"))
    assert len(archives) == 1, archives
    with tarfile.open(archives[0]) as tar:
        names = set(tar.getnames())
    # Relative entries, so the tarball still unpacks over an install.
    assert "SOUL.md" in names
    assert "data/windyfly.db" in names
    assert "data/sounds/its-alive.wav" in names
    assert not any(n.startswith("/") for n in names), names


def test_update_cache_is_cwd_independent(tmp_path, monkeypatch):
    """The 24h update throttle must not reset when the user cds."""
    from windyfly import update

    assert update.CACHE_FILE.is_absolute(), update.CACHE_FILE
    before = update.CACHE_FILE
    monkeypatch.chdir(tmp_path)
    import importlib

    importlib.reload(update)
    try:
        assert update.CACHE_FILE == before
    finally:
        importlib.reload(update)


def test_hatch_recovery_path_is_cwd_independent(tmp_path, monkeypatch):
    """A half-finished hatch must be resumable from any directory."""
    from windyfly import hatch_orchestrator

    assert hatch_orchestrator._RECOVERY_PATH.is_absolute()
    before = hatch_orchestrator._RECOVERY_PATH
    monkeypatch.chdir(tmp_path)
    import importlib

    importlib.reload(hatch_orchestrator)
    try:
        assert hatch_orchestrator._RECOVERY_PATH == before
    finally:
        importlib.reload(hatch_orchestrator)




def test_honours_windyfly_home_override(tmp_path, monkeypatch):
    """Fleet installs and tests pin the root explicitly; the pid file must
    follow it rather than the cwd."""
    monkeypatch.setenv("WINDYFLY_HOME", str(tmp_path))
    monkeypatch.chdir(Path(os.sep))
    assert get_pid_path(get_project_root()) == tmp_path / "data" / "windyfly.pid"
    assert get_data_dir(get_project_root()) == tmp_path / "data"
