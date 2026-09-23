"""First-run journey: a user who just ran `windy go` must be able to chat.

The pre-release clean-machine test for 0.7.2 hit four walls in a row:
`windy chat` was refused ("already running" / "Another Windy Fly runtime is
already hosting this agent") with no hint of what to run, `windy stop`
returned while the brain still held the Mind runtime slot, `/quit` answered
"Unknown command" instead of leaving, and INFO logs scrolled through the
conversation.
"""

from __future__ import annotations

import argparse
import os
from unittest.mock import patch

import pytest

import windyfly.channels.cli as cli_mod
from windyfly import cli
from windyfly.platform import PIDInfo


def _run_cli_with_inputs(inputs, config):
    it = iter(inputs)

    def fake_input(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    with patch.object(cli_mod.console, "input", side_effect=fake_input):
        cli_mod.run_cli(config)


def _never_respond(*_a, **_k):
    raise AssertionError("the agent must not be asked to answer a quit word")


async def _never_route(*_a, **_k):
    raise AssertionError("a quit word must not reach the /command router")


def test_slash_quit_and_exit_leave_the_chat(tmp_path):
    config = {"memory": {"db_path": str(tmp_path / "cli.db")}}
    for word in ("/quit", "/exit", "quit", "EXIT"):
        with patch.object(cli_mod, "agent_respond", side_effect=_never_respond), \
             patch("windyfly.channels.base.handle_incoming", side_effect=_never_route):
            # Anything after the quit word would trip the guards above.
            _run_cli_with_inputs([word, "/status", "hello"], config)


def test_ctrl_d_leaves_the_chat(tmp_path):
    config = {"memory": {"db_path": str(tmp_path / "cli.db")}}
    with patch.object(cli_mod, "agent_respond", side_effect=_never_respond):
        _run_cli_with_inputs([], config)   # first read raises EOFError


def test_already_running_refusal_names_the_exact_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text("")
    (tmp_path / "windyfly.toml").write_text("")
    running = PIDInfo(brain=1, gateway=None)
    monkeypatch.setattr(cli, "read_pid_file", lambda _root: running)
    monkeypatch.setattr(PIDInfo, "any_alive", property(lambda self: True))
    monkeypatch.setattr(PIDInfo, "brain_alive", property(lambda self: True))
    monkeypatch.setattr(PIDInfo, "gateway_alive", property(lambda self: False))
    cli.cmd_start(argparse.Namespace(cli=True, daemon=False, no_browser=True))
    out = capsys.readouterr().out
    assert "windy stop && windy chat" in out


def test_stop_releases_a_slot_the_brain_left_behind(monkeypatch, tmp_path):
    from windyfly import runtime_claim

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("WINDYFLY_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "read_pid_file", lambda _root: PIDInfo(brain=999_999_9))
    monkeypatch.setattr(cli, "process_alive", lambda _pid: False)
    monkeypatch.setattr(cli, "remove_pid_file", lambda _root: None)
    record = runtime_claim.claim_record_path()
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text('{"passport": "ET26-X", "runtime_id": "r1"}')
    calls = []
    monkeypatch.setattr(
        runtime_claim, "release_recorded_claim", lambda **_k: calls.append(1) or True
    )
    cli.cmd_stop(argparse.Namespace())
    assert calls == [1]


def test_interactive_chat_keeps_info_logs_off_the_terminal(monkeypatch, tmp_path, capsys):
    import logging

    from windyfly import main as main_mod

    monkeypatch.setenv("WINDYFLY_HOME", str(tmp_path))
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    root.handlers = []
    try:
        main_mod._configure_logging("cli", "INFO")
        logging.getLogger("windyfly.probe").info("runtime_claim.granted chatter")
        logging.getLogger("windyfly.probe").warning("something the user must see")
        for h in root.handlers:
            h.flush()
        err = capsys.readouterr().err
        assert "chatter" not in err
        assert "something the user must see" in err
        log = (tmp_path / "data" / "cli.log").read_text()
        assert "chatter" in log
    finally:
        for h in root.handlers:
            h.close()
        root.handlers, lvl = saved
        root.setLevel(lvl)


@pytest.mark.skipif(not os.path.exists("/proc/self/stat"), reason="needs /proc")
def test_a_zombie_brain_counts_as_stopped():
    """`windy stop` waited its full timeout on a brain that had already
    exited but was never reaped (container with no init)."""
    import subprocess
    import sys
    import time

    from windyfly.platform import process_alive

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with open(f"/proc/{child.pid}/stat") as fh:
            if fh.read().rsplit(")", 1)[1].split()[0] == "Z":
                break
        time.sleep(0.05)
    try:
        assert process_alive(child.pid) is False
    finally:
        child.wait()
    assert process_alive(os.getpid()) is True
