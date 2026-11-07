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
    with patch("windyfly.platform.find_systemd_unit_for_pattern", return_value=None), \
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
    pid_file.write_text("brain=99991\ngateway=99992\n")
    monkeypatch.setattr(core_mod, "_PID_FILE", pid_file)
    monkeypatch.setattr(core_mod, "_LOCK_FILE", tmp_path / "none.lock")
    with patch("windyfly.platform.find_systemd_unit_for_pattern", return_value=None), \
         patch("windyfly.platform.find_by_name", return_value=OTHERS), \
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
