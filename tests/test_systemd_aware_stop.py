"""Regressions for systemd-aware ``windy stop`` / ``windy kill``.

Pre-fix the journal showed an endless kill↔restart storm:

  Apr 27 06:53:24 windy-0.service: Main process exited, code=killed, status=9/KILL
  Apr 27 06:53:34 Started windy-0.service ...
  Apr 27 06:55:13 ... code=killed, status=9/KILL
  Apr 27 06:55:23 Started windy-0.service ...

Cause: ``cmd_kill`` did ``pkill -9 -f windyfly``, which the unit's
``Restart=on-failure RestartSec=10`` revived ten seconds later. The
fix detects systemd supervision and routes through ``systemctl stop``
instead.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from windyfly.platform import (
    SystemdUnitInfo,
    _parse_systemd_unit_from_cgroup,
    systemctl_stop,
)


# ── Pure cgroup parser ─────────────────────────────────────────────


class TestParseCgroup:
    def test_user_managed_windy_service(self):
        cg = (
            "0::/user.slice/user-1000.slice/user@1000.service/"
            "app.slice/windy-0.service\n"
        )
        assert _parse_systemd_unit_from_cgroup(cg) == ("windy-0.service", "user")

    def test_system_managed_windyfly_service(self):
        cg = "0::/system.slice/windyfly.service\n"
        assert _parse_systemd_unit_from_cgroup(cg) == ("windyfly.service", "system")

    def test_skips_user_at_wrapper(self):
        """``user@1000.service`` is the user-manager wrapper, not an
        application — must skip past it to the leaf service."""
        cg = (
            "0::/user.slice/user-1000.slice/user@1000.service/"
            "app.slice/windy-0.service\n"
        )
        unit, _ = _parse_systemd_unit_from_cgroup(cg)
        assert unit == "windy-0.service"

    def test_no_service_returns_none(self):
        cg = "0::/user.slice/user-1000.slice/session-2.scope\n"
        assert _parse_systemd_unit_from_cgroup(cg) is None

    def test_empty_cgroup_returns_none(self):
        assert _parse_systemd_unit_from_cgroup("") is None


# ── systemctl_stop wrapper ─────────────────────────────────────────


class TestSystemctlStop:
    def test_user_scope_uses_user_flag(self):
        info = SystemdUnitInfo(unit="windy-0.service", scope="user", pid=1234)
        with patch("windyfly.platform.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="", stdout="ok")
            ok, msg = systemctl_stop(info)
        assert ok is True
        assert run.call_args.args[0] == [
            "systemctl", "--user", "stop", "windy-0.service",
        ]

    def test_system_scope_omits_user_flag(self):
        info = SystemdUnitInfo(unit="windyfly.service", scope="system", pid=1234)
        with patch("windyfly.platform.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            systemctl_stop(info)
        assert run.call_args.args[0] == [
            "systemctl", "stop", "windyfly.service",
        ]

    def test_nonzero_return_means_failure(self):
        info = SystemdUnitInfo(unit="windy-0.service", scope="user", pid=1234)
        with patch("windyfly.platform.subprocess.run") as run:
            run.return_value = MagicMock(returncode=5, stderr="not found", stdout="")
            ok, msg = systemctl_stop(info)
        assert ok is False
        assert "not found" in msg


# ── End-to-end on the actual cmd_stop / cmd_kill handlers ──────────


@pytest.fixture
def fresh_commands():
    """Ensure the command registry is freshly populated."""
    from windyfly.commands import core as core_mod
    from windyfly.commands.registry import registry
    registry._commands.clear()
    registry._aliases.clear()
    core_mod._register_all()
    return registry


@pytest.mark.asyncio
async def test_cmd_stop_uses_systemctl_when_systemd_managed(fresh_commands):
    """cmd_stop must NOT pkill when systemd owns the process."""
    info = SystemdUnitInfo(unit="windy-0.service", scope="user", pid=1234)
    with patch(
        "windyfly.platform.find_systemd_unit_for_pattern",
        return_value=info,
    ), patch(
        "windyfly.platform.systemctl_stop",
        return_value=(True, "ok"),
    ) as stop_mock, patch("os.system") as os_system:
        cmd = fresh_commands.get("stop")
        result = await cmd.handler({})

    stop_mock.assert_called_once_with(info)
    os_system.assert_not_called()
    assert "systemctl" in result
    assert "windy-0.service" in result


@pytest.mark.asyncio
async def test_cmd_kill_uses_systemctl_when_systemd_managed(fresh_commands):
    """cmd_kill must NOT pkill -9 when systemd owns the process —
    pkill -9 triggers Restart=on-failure and revives the agent."""
    info = SystemdUnitInfo(unit="windy-0.service", scope="user", pid=1234)
    with patch(
        "windyfly.platform.find_systemd_unit_for_pattern",
        return_value=info,
    ), patch(
        "windyfly.platform.systemctl_stop",
        return_value=(True, "ok"),
    ) as stop_mock, patch("os.system") as os_system:
        cmd = fresh_commands.get("kill")
        result = await cmd.handler({})

    stop_mock.assert_called_once()
    os_system.assert_not_called()
    assert "systemctl" in result
    assert "disable" in result.lower()  # tells user how to prevent boot-time restart


@pytest.mark.asyncio
async def test_cmd_stop_falls_back_when_systemctl_fails(fresh_commands, tmp_path, monkeypatch):
    """If systemctl fails, fall back to PID-file / pkill so a broken
    systemctl doesn't leave the user with no way to stop."""
    monkeypatch.chdir(tmp_path)
    info = SystemdUnitInfo(unit="windy-0.service", scope="user", pid=1234)
    with patch(
        "windyfly.platform.find_systemd_unit_for_pattern",
        return_value=info,
    ), patch(
        "windyfly.platform.systemctl_stop",
        return_value=(False, "Failed to connect to bus"),
    ), patch("os.system") as os_system:
        cmd = fresh_commands.get("stop")
        result = await cmd.handler({})

    # No PID file in tmp_path, so the os.system fallback fires.
    assert os_system.called
    assert "fallback" in result.lower() or "stopped" in result.lower()


@pytest.mark.asyncio
async def test_cmd_stop_uses_pidfile_when_not_systemd_managed(fresh_commands, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "windyfly.pid").write_text("brain=99999\n")

    with patch(
        "windyfly.platform.find_systemd_unit_for_pattern",
        return_value=None,
    ), patch("os.kill") as os_kill, patch("os.system") as os_system:
        cmd = fresh_commands.get("stop")
        result = await cmd.handler({})

    os_system.assert_not_called()
    os_kill.assert_called()
    assert "stopped" in result.lower()
