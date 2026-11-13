"""The whole-host shell (shell.exec sandbox=host_rw) is OFF unless the soul turns it on (Boss, BOARD
2026-11-13T01:57:08Z): [capabilities.shell] host_rw = true. Off, the tool does not offer it and refuses it even for
the owner; on, the owner's turn runs it as before. The Docker sandbox is unchanged either way.

Also: ssh.exec runs the same pre-flight blocklist, so a command naming the owner's credentials store is refused
before any ssh (Hub, BOARD 2026-11-13T01:55:17Z).
"""

from __future__ import annotations

import pytest

from windyfly.agent.capabilities.descriptor import Band
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.agent.capabilities.shell import HOST_RW_OFF, host_rw_enabled, register_shell_capabilities
from windyfly.tools.bands import turn_band


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))


def _shell(config: dict):
    r = CapabilityRegistry()
    register_shell_capabilities(r, config=config)
    return r.get("shell.exec")


@pytest.mark.parametrize("value, on", [
    (None, False), (False, False), ("false", False), ("maybe", False), (0, False), ("", False),
    (True, True), ("true", True), ("YES", True), ("on", True), ("1", True), (1, True),
])
def test_only_a_real_yes_turns_it_on(value, on: bool) -> None:
    cfg = {} if value is None else {"host_rw": value}
    assert host_rw_enabled(cfg) is on


@pytest.mark.parametrize("config", [{}, {"capabilities": {}}, {"capabilities": {"shell": {}}},
                                    {"capabilities": {"shell": {"host_rw": False}}}])
def test_off_by_default_the_owner_is_refused_and_nothing_runs(config: dict) -> None:
    cap = _shell(config)
    with turn_band(Band.OWNER):
        with pytest.raises(PermissionError) as e:
            cap.handler(command="echo should-not-run", sandbox="host_rw")
    assert str(e.value) == HOST_RW_OFF


def test_off_the_tool_does_not_offer_it() -> None:
    cap = _shell({})
    assert cap.input_schema["properties"]["sandbox"]["enum"] == ["docker"]
    assert "host_rw" not in cap.description


def test_on_the_owner_runs_it_as_before() -> None:
    """The control: the same call, the same band, with the soul's yes, runs on the host."""
    cap = _shell({"capabilities": {"shell": {"host_rw": True}}})
    assert "host_rw" in cap.input_schema["properties"]["sandbox"]["enum"]
    assert "host_rw" in cap.description
    with turn_band(Band.OWNER):
        out = cap.handler(command="echo host-ok", sandbox="host_rw")
    assert out["exit_code"] == 0 and "host-ok" in str(out["stdout"])


def test_on_a_non_owner_is_still_refused() -> None:
    cap = _shell({"capabilities": {"shell": {"host_rw": True}}})
    with turn_band(Band.TRUSTED):
        with pytest.raises(PermissionError, match="requires OWNER"):
            cap.handler(command="echo x", sandbox="host_rw")


def test_ssh_exec_refuses_a_command_naming_the_lockbox_before_any_ssh(monkeypatch) -> None:
    import windyfly.agent.capabilities.ssh as ssh

    def _no_ssh(*a, **k):  # pragma: no cover - reaching it is the failure
        raise AssertionError("ssh ran")

    monkeypatch.setattr(ssh.subprocess, "run", _no_ssh)
    with pytest.raises(PermissionError, match="blocked: .*lockbox"):
        ssh._ssh_exec_handler(host="box.example", command="cat ~/kit-army-config/ACCESS_LOCKBOX.md")
