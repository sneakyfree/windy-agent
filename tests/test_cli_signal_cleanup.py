"""Wave 12 P1 — SIGTERM cleanup for daemon children (finding #11).

The bug: `windy go --key sk-test --byok &; PID=$!; kill $PID` left an
orphan `bun run src/server.ts` process bound to :3000 because daemon
children are spawned into a new session.

The fix: install a SIGTERM/SIGINT handler on the parent that walks the
tracked-PID list and terminates each child; atexit is a safety net
when the signal handler trips but cleanup doesn't finish.

These tests exercise the helper functions directly — spawning real
child processes in pytest is flaky across platforms, and the handler
behaviour is what we actually want to pin.
"""

from __future__ import annotations

import signal
from unittest.mock import patch

import pytest

from windyfly import cli as cli_mod


@pytest.fixture(autouse=True)
def reset_tracking():
    """Scrub module state between tests so signal-handler installation
    and the tracked-PID list don't bleed across cases."""
    cli_mod._tracked_child_pids.clear()
    cli_mod._cleanup_already_ran = False
    cli_mod._cleanup_requested_via_signal = False
    cli_mod._signal_cleanup_installed = False
    # Restore default disposition so repeated installs don't observe
    # a lingering handler from a previous test.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    yield
    cli_mod._tracked_child_pids.clear()
    cli_mod._cleanup_already_ran = False
    cli_mod._cleanup_requested_via_signal = False
    cli_mod._signal_cleanup_installed = False
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)


def test_cleanup_terminates_every_tracked_pid() -> None:
    cli_mod._tracked_child_pids.extend([111, 222, 333])
    terminated: list[int] = []
    with patch.object(cli_mod, "process_alive", return_value=True), \
         patch.object(cli_mod, "process_terminate",
                      side_effect=lambda pid: terminated.append(pid) or True), \
         patch.object(cli_mod, "remove_pid_file"):
        cli_mod._cleanup_tracked_children()
    assert terminated == [111, 222, 333]


def test_cleanup_skips_dead_children() -> None:
    cli_mod._tracked_child_pids.extend([111, 222])
    terminated: list[int] = []
    alive_by_pid = {111: True, 222: False}
    with patch.object(cli_mod, "process_alive", side_effect=lambda p: alive_by_pid[p]), \
         patch.object(cli_mod, "process_terminate",
                      side_effect=lambda pid: terminated.append(pid) or True), \
         patch.object(cli_mod, "remove_pid_file"):
        cli_mod._cleanup_tracked_children()
    assert terminated == [111]


def test_cleanup_is_idempotent() -> None:
    cli_mod._tracked_child_pids.append(111)
    terminated: list[int] = []
    with patch.object(cli_mod, "process_alive", return_value=True), \
         patch.object(cli_mod, "process_terminate",
                      side_effect=lambda pid: terminated.append(pid) or True), \
         patch.object(cli_mod, "remove_pid_file"):
        cli_mod._cleanup_tracked_children()
        cli_mod._cleanup_tracked_children()  # second call → no-op
    assert terminated == [111]


def test_cleanup_tolerates_terminate_failure() -> None:
    cli_mod._tracked_child_pids.append(111)
    with patch.object(cli_mod, "process_alive", return_value=True), \
         patch.object(cli_mod, "process_terminate",
                      side_effect=OSError("pid not visible to this process")), \
         patch.object(cli_mod, "remove_pid_file"):
        # Must not raise — cleanup is best-effort.
        cli_mod._cleanup_tracked_children()


def test_install_signal_cleanup_registers_sigterm_and_sigint() -> None:
    cli_mod._install_signal_cleanup()
    # Each signal should now point at our handler, not SIG_DFL / SIG_IGN.
    sigterm_handler = signal.getsignal(signal.SIGTERM)
    sigint_handler = signal.getsignal(signal.SIGINT)
    assert sigterm_handler is cli_mod._signal_cleanup_handler
    assert sigint_handler is cli_mod._signal_cleanup_handler


def test_install_signal_cleanup_is_idempotent() -> None:
    cli_mod._install_signal_cleanup()
    # Re-installing must NOT replace or duplicate; the flag prevents that.
    cli_mod._install_signal_cleanup()
    assert cli_mod._signal_cleanup_installed is True


def test_signal_handler_runs_cleanup_and_reraises() -> None:
    cli_mod._tracked_child_pids.append(555)
    terminated: list[int] = []
    with patch.object(cli_mod, "process_alive", return_value=True), \
         patch.object(cli_mod, "process_terminate",
                      side_effect=lambda pid: terminated.append(pid) or True), \
         patch.object(cli_mod, "remove_pid_file"), \
         patch.object(cli_mod.os, "kill") as mock_kill, \
         patch.object(cli_mod.signal, "signal") as mock_signal:
        cli_mod._signal_cleanup_handler(signal.SIGTERM, None)

    # 1. Cleanup ran.
    assert terminated == [555]
    # 2. The flag was set so the atexit safety net knows we handled it.
    assert cli_mod._cleanup_requested_via_signal is True
    # 3. Default disposition restored before the re-raise …
    mock_signal.assert_any_call(signal.SIGTERM, signal.SIG_DFL)
    # 4. … and the signal was re-raised at the current PID.
    mock_kill.assert_called_once()
    _, kwargs = mock_kill.call_args
    # os.kill(pid, signum) — positional only; inspect args.
    args = mock_kill.call_args.args
    assert args[1] == signal.SIGTERM


def test_atexit_safety_net_runs_only_when_signal_tripped() -> None:
    """Normal exit path: atexit must NOT kill children (daemon-survives-
    terminal-close promise). Only run if the signal handler flagged it."""
    cli_mod._tracked_child_pids.append(999)
    cli_mod._cleanup_requested_via_signal = False
    terminated: list[int] = []
    with patch.object(cli_mod, "process_terminate",
                      side_effect=lambda pid: terminated.append(pid) or True), \
         patch.object(cli_mod, "process_alive", return_value=True), \
         patch.object(cli_mod, "remove_pid_file"):
        cli_mod._atexit_safety_net()
    assert terminated == [], "atexit must not kill children on a normal exit"


def test_atexit_safety_net_finishes_interrupted_cleanup() -> None:
    cli_mod._tracked_child_pids.append(999)
    cli_mod._cleanup_requested_via_signal = True
    cli_mod._cleanup_already_ran = False  # signal handler started but didn't finish
    terminated: list[int] = []
    with patch.object(cli_mod, "process_terminate",
                      side_effect=lambda pid: terminated.append(pid) or True), \
         patch.object(cli_mod, "process_alive", return_value=True), \
         patch.object(cli_mod, "remove_pid_file"):
        cli_mod._atexit_safety_net()
    assert terminated == [999]
