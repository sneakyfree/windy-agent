"""ssh.exec capability tests — Tier 0 Stock Toolkit.

Pins the contract Grant care about:

  1. Bot can SSH to fleet hosts (the immediate gap, surfaced 2026-05-12
     when Windy 0 told him "I don't have an SSH tool").
  2. Loopback is rejected (use shell.exec for local — no sandbox-bypass).
  3. Pre-flight blocklist screens catastrophic commands.
  4. Timeout enforced, output capped, non-zero exit is data not exception.
  5. Whitelisted hosts run at USER band; unknown hosts require OWNER.
  6. Missing system ssh binary surfaces a clear error.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from windyfly.agent.capabilities.descriptor import (
    Band,
    Tier,
)
from windyfly.agent.capabilities.ssh import (
    _allowed_hosts,
    _host_is_allowed,
    _host_is_loopback,
    _ssh_exec_handler,
    _ssh_runtime_tier_check,
    register_ssh_capabilities,
)


# ─── Loopback rejection ───────────────────────────────────────────


class TestLoopbackRejection:
    """SSH to loopback bypasses shell.exec's Docker sandbox. Block it."""

    @pytest.mark.parametrize("host", [
        "localhost",
        "127.0.0.1",
        "127.1.2.3",
        "::1",
        "0.0.0.0",
        "LOCALHOST",  # case-insensitive
        "me@localhost",  # with user prefix
        "root@127.0.0.1",
    ])
    def test_loopback_detected(self, host):
        assert _host_is_loopback(host), \
            f"{host!r} should be detected as loopback"

    @pytest.mark.parametrize("host", [
        "10.10.0.6",
        "wg-0c2",
        "veron1",
        "user@example.com",
        "192.168.1.10",
    ])
    def test_non_loopback_passes(self, host):
        assert not _host_is_loopback(host), \
            f"{host!r} should not be flagged as loopback"

    def test_handler_refuses_loopback(self):
        with pytest.raises(PermissionError, match="loopback"):
            _ssh_exec_handler(host="localhost", command="whoami")


# ─── Whitelist gating ─────────────────────────────────────────────


class TestWhitelistGating:
    """Hosts in WINDY_SSH_ALLOWED_HOSTS run at USER; others bump to OWNER."""

    def test_allowed_hosts_parses_env(self, monkeypatch):
        monkeypatch.setenv(
            "WINDY_SSH_ALLOWED_HOSTS",
            "wg-0c2, wg-0c3,oc1-gpu@10.10.0.6,10.10.0.5",
        )
        allowed = _allowed_hosts()
        assert "wg-0c2" in allowed
        assert "wg-0c3" in allowed
        assert "oc1-gpu@10.10.0.6" in allowed
        assert "10.10.0.5" in allowed

    def test_unset_env_means_empty_set(self, monkeypatch):
        monkeypatch.delenv("WINDY_SSH_ALLOWED_HOSTS", raising=False)
        assert _allowed_hosts() == set()

    def test_exact_match_allowed(self, monkeypatch):
        monkeypatch.setenv("WINDY_SSH_ALLOWED_HOSTS", "wg-0c2,wg-0c3")
        assert _host_is_allowed("wg-0c2") is True
        assert _host_is_allowed("wg-0c4") is False

    def test_user_prefix_matches_against_bare_host(self, monkeypatch):
        monkeypatch.setenv("WINDY_SSH_ALLOWED_HOSTS", "10.10.0.6")
        assert _host_is_allowed("oc1-gpu@10.10.0.6") is True
        assert _host_is_allowed("10.10.0.6") is True

    def test_runtime_tier_check_no_override_for_allowed(self, monkeypatch):
        monkeypatch.setenv("WINDY_SSH_ALLOWED_HOSTS", "wg-0c2")
        # None means "keep the static tier" (EXTERNAL_EFFECT → TRUSTED)
        assert _ssh_runtime_tier_check({"host": "wg-0c2"}) is None

    def test_runtime_tier_check_bumps_unknown_host(self, monkeypatch):
        monkeypatch.setenv("WINDY_SSH_ALLOWED_HOSTS", "wg-0c2")
        # Bumps to FULL_MACHINE (OWNER required)
        bumped = _ssh_runtime_tier_check({"host": "stranger.example.com"})
        assert bumped == Tier.FULL_MACHINE


# ─── Input validation ─────────────────────────────────────────────


class TestInputValidation:

    def test_empty_host_rejected(self):
        with pytest.raises(ValueError, match="host"):
            _ssh_exec_handler(host="", command="whoami")

    def test_empty_command_rejected(self):
        with pytest.raises(ValueError, match="command"):
            _ssh_exec_handler(host="wg-0c2", command="")

    def test_whitespace_only_command_rejected(self):
        with pytest.raises(ValueError, match="command"):
            _ssh_exec_handler(host="wg-0c2", command="   ")


# ─── Blocklist screen ─────────────────────────────────────────────


class TestBlocklistScreen:
    """The same shell.exec blocklist screens remote commands too."""

    @pytest.mark.parametrize("command", [
        "rm -rf /",
        ":(){:|:&};:",   # fork bomb
    ])
    def test_catastrophic_commands_blocked(self, command):
        with pytest.raises(PermissionError, match="blocked"):
            _ssh_exec_handler(host="wg-0c2", command=command)


# ─── Subprocess invocation shape ──────────────────────────────────


class TestSubprocessShape:
    """The handler shells to the system `ssh` binary with safe flags."""

    def test_argv_includes_strict_host_key_check(self):
        captured = {}

        def fake_run(argv, capture_output, timeout):
            captured["argv"] = argv
            captured["timeout"] = timeout
            # Mimic a successful run
            class R:
                returncode = 0
                stdout = b"ok\n"
                stderr = b""
            return R()

        with patch("subprocess.run", side_effect=fake_run):
            _ssh_exec_handler(host="wg-0c2", command="echo ok")

        argv = captured["argv"]
        assert argv[0] == "ssh"
        # Safe-by-default flags
        assert "StrictHostKeyChecking=accept-new" in argv
        assert "ConnectTimeout=10" in argv
        assert "BatchMode=yes" in argv  # no interactive password prompts
        assert "wg-0c2" in argv
        assert "echo ok" in argv

    def test_timeout_is_passed_through(self):
        captured = {}

        def fake_run(argv, capture_output, timeout):
            captured["timeout"] = timeout
            class R:
                returncode = 0; stdout = b""; stderr = b""
            return R()

        with patch("subprocess.run", side_effect=fake_run):
            _ssh_exec_handler(host="wg-0c2", command="ls", timeout_s=45)
        assert captured["timeout"] == 45

    def test_timeout_clamped_to_ceiling(self):
        captured = {}

        def fake_run(argv, capture_output, timeout):
            captured["timeout"] = timeout
            class R:
                returncode = 0; stdout = b""; stderr = b""
            return R()

        with patch("subprocess.run", side_effect=fake_run):
            _ssh_exec_handler(host="wg-0c2", command="ls", timeout_s=9999)
        assert captured["timeout"] == 300  # ceiling

    def test_timeout_clamped_to_min(self):
        captured = {}

        def fake_run(argv, capture_output, timeout):
            captured["timeout"] = timeout
            class R:
                returncode = 0; stdout = b""; stderr = b""
            return R()

        with patch("subprocess.run", side_effect=fake_run):
            _ssh_exec_handler(host="wg-0c2", command="ls", timeout_s=0)
        assert captured["timeout"] == 1


# ─── Result shape ─────────────────────────────────────────────────


class TestResultShape:

    def test_success_returns_full_shape(self):
        def fake_run(argv, capture_output, timeout):
            class R:
                returncode = 0
                stdout = b"hello from veron-1\n"
                stderr = b""
            return R()

        with patch("subprocess.run", side_effect=fake_run):
            result = _ssh_exec_handler(host="wg-veron", command="hostname")

        assert result["host"] == "wg-veron"
        assert result["command"] == "hostname"
        assert result["exit_code"] == 0
        assert result["stdout"] == "hello from veron-1\n"
        assert result["stderr"] == ""
        assert result["stdout_truncated"] is False
        assert result["stderr_truncated"] is False
        assert result["timed_out"] is False
        assert result["outcome_score"] == 1.0
        assert "duration_ms" in result

    def test_nonzero_exit_is_data_not_exception(self):
        def fake_run(argv, capture_output, timeout):
            class R:
                returncode = 1
                stdout = b""
                stderr = b"command not found\n"
            return R()

        with patch("subprocess.run", side_effect=fake_run):
            result = _ssh_exec_handler(host="wg-0c2", command="nonsense")

        assert result["exit_code"] == 1
        assert "command not found" in result["stderr"]
        assert result["outcome_score"] == 0.0

    def test_timeout_returns_timed_out_true(self):
        def fake_run(argv, capture_output, timeout):
            raise subprocess.TimeoutExpired(
                cmd=argv, timeout=timeout,
                output=b"partial output", stderr=b"",
            )

        with patch("subprocess.run", side_effect=fake_run):
            result = _ssh_exec_handler(
                host="wg-0c2", command="sleep 60", timeout_s=2,
            )

        assert result["timed_out"] is True
        assert result["exit_code"] == -1
        assert result["stdout"] == "partial output"
        assert result["outcome_score"] == 0.0

    def test_output_truncated_at_cap(self):
        big = b"x" * (200 * 1024)  # 200KB

        def fake_run(argv, capture_output, timeout):
            class R:
                returncode = 0
                stdout = big
                stderr = b""
            return R()

        with patch("subprocess.run", side_effect=fake_run):
            result = _ssh_exec_handler(host="wg-0c2", command="big")

        assert len(result["stdout"]) == 64 * 1024
        assert result["stdout_truncated"] is True


# ─── Missing ssh binary ───────────────────────────────────────────


class TestMissingSshBinary:

    def test_missing_ssh_surfaces_clear_error(self):
        def fake_run(argv, capture_output, timeout):
            raise FileNotFoundError("[Errno 2] No such file: 'ssh'")

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="openssh-client|ssh.*PATH"):
                _ssh_exec_handler(host="wg-0c2", command="whoami")


# ─── Registration smoke ───────────────────────────────────────────


class TestRegistration:
    """The boot step actually registers ssh.exec in the capability registry."""

    def test_register_adds_capability(self):
        from windyfly.agent.capabilities.registry import CapabilityRegistry
        reg = CapabilityRegistry()
        register_ssh_capabilities(reg, {})
        ids = {cap.id for cap in reg.all()}
        assert "ssh.exec" in ids

    def test_registered_cap_has_runtime_tier_check(self):
        from windyfly.agent.capabilities.registry import CapabilityRegistry
        reg = CapabilityRegistry()
        register_ssh_capabilities(reg, {})
        cap = next(c for c in reg.all() if c.id == "ssh.exec")
        # Static tier defaults to EXTERNAL_EFFECT (TRUSTED band)
        assert cap.tier == Tier.EXTERNAL_EFFECT
        # And the runtime check is wired so unknown hosts escalate
        assert cap.runtime_tier_check is not None
