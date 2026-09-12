"""The suite must never stop the live agent on a systemd machine.

Observed 2026-09-12: a full-suite run on Windy 0 stopped
``windy-0@matrix.service`` at 14:09:52 with a clean ``systemctl stop``
(the journal logged "Stopping…", the agent logged a graceful SIGTERM
shutdown) and it stayed down — a deliberate stop also marks the unit
inactive, so ``Restart=`` never fires. Telegram, in the same cgroup
pattern, survived, which rules out a broad ``pkill -f windyfly``.

The exact caller was never identified. ``platform.systemctl_stop``
traced zero calls on a re-run, no test references ``fire-drill.sh``
(the one script that stops this unit by name), and the guardian — the
other component that knows the per-channel unit names — only ever
restarts, and was not running. So rather than guard the one door we
could name, the guard covers the class:

  - the call sites we know (``systemctl_stop``, the ``os.system``
    ``pkill`` fall-throughs, ``kill_by_name``, ``schedule_restart``)
  - and ``subprocess.run`` itself, the chokepoint all of them funnel
    through, so an unknown or future caller is covered too.

These tests pin the guard. They deliberately do NOT patch the things
under test — that is the whole point: if conftest stops neutralizing
them, the real ones run and these tests notice.
"""

from __future__ import annotations

import sys
from unittest.mock import Mock, patch

import pytest

from windyfly.platform import SystemdUnitInfo


# ── The guard is installed ────────────────────────────────────────


def test_systemctl_stop_is_neutralized_by_default():
    """The production lookup path must resolve to a mock, not the real
    function. core.py imports it inside the function body, so this is
    the attribute it will actually get."""
    import windyfly.platform as platform_mod

    assert isinstance(platform_mod.systemctl_stop, Mock), (
        "tests/conftest.py must neutralize windyfly.platform.systemctl_stop "
        "— without it a suite run stops the live agent's systemd unit"
    )


def test_os_system_is_neutralized_by_default():
    """``pkill -f windyfly`` fall-throughs must not reach the shell."""
    import os

    assert isinstance(os.system, Mock), (
        "tests/conftest.py must neutralize os.system — cmd_stop/cmd_kill "
        "fall through to `pkill -f 'windyfly'` when the systemd branch "
        "is not taken"
    )


# ── The guard actually protects cmd_stop / cmd_kill ───────────────


@pytest.fixture
def fresh_commands():
    from windyfly.commands import core as core_mod
    from windyfly.commands.registry import registry

    registry._commands.clear()
    registry._aliases.clear()
    core_mod._register_all()
    return registry


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["stop", "kill"])
async def test_live_unit_is_never_really_stopped(fresh_commands, command):
    """With a live-looking unit discovered, the command must report
    success without the real systemctl or a shell pkill running.

    Only ``find_systemd_unit_for_pattern`` is patched here — it stands in
    for "this machine has a running agent". Everything downstream is
    whatever conftest left in place.
    """
    import os

    info = SystemdUnitInfo(unit="windy-0@matrix.service", scope="user", pid=4242)
    with patch(
        "windyfly.platform.find_systemd_unit_for_pattern",
        return_value=info,
    ):
        result = await fresh_commands.get(command).handler({})

    # Took the systemd branch (so it never reached the pkill fall-through)...
    assert "systemctl" in result
    assert "windy-0@matrix.service" in result
    # ...and did it without shelling out.
    assert not os.system.called


# ── The chokepoint guard ──────────────────────────────────────────
#
# The call-site guards above only cover callers we know about. These pin
# the backstop: any code that shells out to a destructive systemctl is
# stopped at subprocess.run, whoever it is.


@pytest.mark.parametrize(
    "argv",
    [
        ["systemctl", "--user", "stop", "windy-0@matrix.service"],
        ["systemctl", "--user", "restart", "windy-0@telegram.service"],
        ["systemctl", "--user", "disable", "--now", "windy-0@matrix.service"],
        ["systemctl", "kill", "windy-0@matrix.service"],
        ["/usr/bin/systemctl", "--user", "stop", "windy-0@matrix.service"],
    ],
)
def test_destructive_systemctl_is_intercepted(argv):
    """These must never reach the real systemctl — they would take the
    live agent down on any developer's machine."""
    import subprocess

    result = subprocess.run(argv, capture_output=True, text=True)

    assert result.returncode == 0, "guard should report benign success"
    assert result.stdout == "", "guard should not produce real output"


def test_readonly_systemctl_still_runs_for_real():
    """Only destructive verbs are intercepted. Tests that ask the host
    a question must still get the real answer, or the guard would be
    silently faking the world."""
    import shutil
    import subprocess

    if shutil.which("systemctl") is None:
        pytest.skip("no systemctl on this platform")

    # A unit that cannot exist: real systemctl says "unknown"/non-zero.
    # The guard, if it were intercepting, would hand back 0 + "".
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "windy-does-not-exist-xyz.service"],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0 or result.stdout.strip() != ""


def test_non_systemctl_commands_are_untouched():
    """The guard keys on the program name, not the verb — `git stop`
    or a script named with a destructive word must still run."""
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", "print('stop restart disable')"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "stop restart disable" in result.stdout
