"""Exact-PID rule (Boss's sandbox hardening list): nothing in the agent stops processes BY NAME. A name pattern also
matches other agents on the same machine (a lab agent run from the same folder) and once stopped a production agent.
With no systemd unit and no PID file, /stop and /kill name the candidates and stop nothing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "windyfly"
OTHERS = [(4242, "python -m windyfly.main --channel matrix --config /x/lab.toml")]


def test_no_source_file_kills_by_name() -> None:
    for p in SRC.rglob("*.py"):
        code = "\n".join(ln for ln in p.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))
        for bad in ('os.system("pkill', "os.system('pkill", '["pkill"', "['pkill'", '"killall"', "'killall'"):
            assert bad not in code, f"{p.relative_to(SRC)} still kills by name ({bad})"


@pytest.fixture
def fresh_commands():
    from windyfly.commands import core as core_mod
    from windyfly.commands.registry import registry
    registry._commands.clear()
    registry._aliases.clear()
    core_mod._register_all()
    return registry


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["stop", "kill"])
async def test_no_unit_no_pid_file_kills_nothing_and_names_candidates(fresh_commands, tmp_path, monkeypatch, name):
    from windyfly.commands import core as core_mod
    monkeypatch.setattr(core_mod, "_PID_FILE", tmp_path / "none.pid")
    monkeypatch.setattr(core_mod, "_LOCK_FILE", tmp_path / "none.lock")
    with patch("windyfly.platform.own_systemd_unit", return_value=None), \
         patch("windyfly.platform.find_by_name", return_value=OTHERS), \
         patch("os.kill") as kill, patch("os.system") as system:
        out = await fresh_commands.get(name).handler({})
    kill.assert_not_called()
    system.assert_not_called()
    assert "4242" in out and "PID" in out and "didn't stop anything" in out


@pytest.mark.asyncio
async def test_kill_uses_only_the_pid_file(fresh_commands, tmp_path, monkeypatch):
    from windyfly.commands import core as core_mod
    pid_file = tmp_path / "windyfly.pid"
    pid_file.write_text("brain=99991\ngateway=99992\nstarted=2026-11-07T16:00:00Z\n")
    monkeypatch.setattr(core_mod, "_PID_FILE", pid_file)
    monkeypatch.setattr(core_mod, "_LOCK_FILE", tmp_path / "none.lock")
    with patch("windyfly.platform.own_systemd_unit", return_value=None), \
         patch("windyfly.platform.find_by_name", return_value=OTHERS), \
         patch("windyfly.platform.pid_is_ours", return_value=True), \
         patch("os.kill") as kill, patch("os.system") as system:
        out = await fresh_commands.get("kill").handler({})
    system.assert_not_called()
    assert sorted(c.args[0] for c in kill.call_args_list) == [99991, 99992]   # never 4242
    assert "2 process" in out


def test_find_by_name_lists_but_never_kills() -> None:
    from windyfly import platform
    with patch("os.kill") as kill:
        platform.find_by_name(["windyfly.main"])
    kill.assert_not_called()


def test_cli_fallback_names_processes_instead_of_killing(capsys) -> None:
    import windyfly.cli as cli
    with patch.object(cli, "find_by_name", return_value=OTHERS), patch("os.kill") as kill:
        cli._do_kill_by_name()
    kill.assert_not_called()
    out = capsys.readouterr().out
    assert "4242" in out and "Not stopping anything by name" in out


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["stop", "kill"])
async def test_a_stale_pid_in_the_pid_file_is_never_signalled(fresh_commands, tmp_path, monkeypatch, name):
    """After a crash or a reboot the PID file's number can belong to an unrelated process (Hub on #753)."""
    from windyfly.commands import core as core_mod
    pid_file = tmp_path / "windyfly.pid"
    pid_file.write_text("brain=99991\nstarted=2026-11-07T16:00:00Z\n")
    monkeypatch.setattr(core_mod, "_PID_FILE", pid_file)
    monkeypatch.setattr(core_mod, "_LOCK_FILE", tmp_path / "none.lock")
    with patch("windyfly.platform.own_systemd_unit", return_value=None), \
         patch("windyfly.platform.find_by_name", return_value=[]), \
         patch("windyfly.platform.pid_is_ours", return_value=False), \
         patch("os.kill") as kill:
        out = await fresh_commands.get(name).handler({})
    kill.assert_not_called()
    assert "Nothing to stop" in out


def test_pid_is_ours_checks_uid_and_command_line():
    import os
    import subprocess
    import sys

    from windyfly.platform import pid_is_ours
    assert pid_is_ours(os.getpid()) is False or "windyfly" in " ".join(sys.argv)   # pytest itself: not a windyfly cmd
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert pid_is_ours(child.pid) is False          # ours by uid, but not a Windy Fly command line
    finally:
        child.kill()
        child.wait()
    assert pid_is_ours(1) is False                      # another user's process
    assert pid_is_ours(0) is False and pid_is_ours(-5) is False


@pytest.mark.parametrize("leaf,expected", [
    ("windy-0@matrix.service", "windy-0@matrix.service"),
    ("windyfly.service", "windyfly.service"),
    ("zero-lab.service", None),                  # not a name windy installs: fall through to the PID file
    ("postgresql.service", None),                # started by hand inside another service: never stop it
])
def test_own_unit_is_only_a_unit_windy_installs(monkeypatch, leaf, expected):
    from pathlib import Path as _P

    import windyfly.platform as plat
    monkeypatch.setattr(plat, "IS_LINUX", True)
    monkeypatch.delenv("WINDY_SYSTEMD_UNIT", raising=False)
    real_read = _P.read_text

    def fake_read(self, *a, **k):
        if str(self) == "/proc/self/cgroup":
            return f"0::/user.slice/user-1000.slice/user@1000.service/app.slice/{leaf}\n"
        return real_read(self, *a, **k)

    monkeypatch.setattr(_P, "read_text", fake_read)
    info = plat.own_systemd_unit()
    assert (info.unit if info else None) == expected
    monkeypatch.setenv("WINDY_SYSTEMD_UNIT", leaf)
    assert plat.own_systemd_unit().unit == leaf  # an install that names its own unit


def test_own_unit_reads_only_this_process_cgroup(monkeypatch, tmp_path):
    from pathlib import Path as _P

    import windyfly.platform as plat
    monkeypatch.setattr(plat, "IS_LINUX", True)
    real_read = _P.read_text

    def fake_read(self, *a, **k):
        if str(self) == "/proc/self/cgroup":
            return "0::/user.slice/user-1000.slice/user@1000.service/app.slice/windy-lab.service\n"
        return real_read(self, *a, **k)

    monkeypatch.setattr(_P, "read_text", fake_read)
    info = plat.own_systemd_unit()
    assert info is not None and info.unit == "windy-lab.service" and info.scope == "user"


@pytest.mark.parametrize("cmd", ["stop", "kill"])
def test_cli_never_signals_a_name_match(cmd, tmp_path, monkeypatch, capsys):
    """The CLI's stop/kill with no PID file: find_by_name results are listed, never signalled."""
    import windyfly.cli as cli
    monkeypatch.setattr(cli, "read_pid_file", lambda root: None)
    with patch.object(cli, "find_by_name", return_value=OTHERS), patch("os.kill") as kill, \
         patch("windyfly.platform.force_kill") as fk, patch.object(cli, "process_terminate") as pt:
        import argparse
        (cli.cmd_stop if cmd == "stop" else cli._cmd_kill)(argparse.Namespace())
    kill.assert_not_called()
    fk.assert_not_called()
    pt.assert_not_called()
    assert "4242" in capsys.readouterr().out
