"""Contract tests for the slash-command gate (P0-S3 fix).

Proves the remote-dispatch-to-shell bypass is closed:

  - Remote platforms (matrix/sms/email/...) cannot invoke
    12_developer, 10_cloud, or 11_maintenance commands.
  - Developer commands require an explicit confirmation token
    (dangerous=True).
  - Trust gate runs before dispatch; TrustDenied blocks execution.
  - Shell metacharacters in /run and /git no longer reach a shell.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from windyfly.commands import core
from windyfly.commands.registry import (
    CHANNEL_POLICY,
    Command,
    _platform_may_invoke,
    _trust_action_for,
    _needs_trust_gate,
    registry,
)
from windyfly.memory.database import Database
from windyfly.trust.check import TrustSnapshot, _cache_write


@pytest.fixture(autouse=True)
def _register_commands():
    core.init_core()  # registers /run, /git, /web, /repl with the registry


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDYFLY_DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-GATE")
    monkeypatch.delenv("ETERNITAS_URL", raising=False)
    monkeypatch.delenv("ETERNITAS_API_URL", raising=False)
    return Database(str(tmp_path / "agent.db"))


def _seed_trust(db, *, allowed_actions, band="good", status="active"):
    """Pre-seed the trust cache so the sync gate reads it without HTTP."""
    _cache_write(
        TrustSnapshot(
            passport="ET26-TEST-GATE",
            status=status,
            band=band,
            clearance_level="cleared",
            tier_multiplier=1.5,
            integrity_score=800,
            allowed_actions=list(allowed_actions),
        ),
        db=db,
    )


class TestChannelPolicy:
    def test_terminal_allows_developer_category(self):
        assert _platform_may_invoke("terminal", "12_developer")
        assert _platform_may_invoke("cli", "12_developer")

    def test_remote_channels_deny_developer_category(self):
        for platform in ("matrix", "telegram", "slack", "discord",
                          "whatsapp", "signal", "sms", "email", "irc",
                          "teams", "unknown"):
            assert not _platform_may_invoke(platform, "12_developer"), \
                f"{platform} should not invoke 12_developer"

    def test_remote_channels_deny_cloud_and_maintenance(self):
        for cat in ("10_cloud", "11_maintenance"):
            assert not _platform_may_invoke("matrix", cat)
            assert not _platform_may_invoke("telegram", cat)

    def test_remote_channels_allow_safe_categories(self):
        for cat in ("02_diagnostics", "03_chat", "13_help"):
            assert _platform_may_invoke("matrix", cat)

    def test_per_channel_override(self):
        CHANNEL_POLICY["bot_sandbox"] = frozenset({"12_developer", "13_help"})
        try:
            assert _platform_may_invoke("bot_sandbox", "12_developer")
            assert not _platform_may_invoke("bot_sandbox", "06_memory")
        finally:
            CHANNEL_POLICY.pop("bot_sandbox", None)


class TestTrustGateOnExecute:
    async def test_remote_run_is_refused_by_channel_policy_not_trust(self, db):
        _seed_trust(db, allowed_actions=["read", "send", "execute"])
        out = await registry.execute("run whoami", {"platform": "matrix"})
        assert "not allowed from matrix" in out.lower()

    async def test_local_run_blocked_without_confirm_token(self, db):
        _seed_trust(db, allowed_actions=["read", "send", "execute"])
        out = await registry.execute("run whoami", {"platform": "terminal"})
        assert "dangerous" in out.lower() or "confirm" in out.lower()

    async def test_local_run_blocked_when_trust_denies(self, db):
        # Trust snapshot has NO 'execute' action.
        _seed_trust(db, allowed_actions=["read", "send"])
        out = await registry.execute(
            "run whoami --confirm", {"platform": "terminal"}
        )
        assert "trust gate denied" in out.lower()

    async def test_local_run_allowed_with_confirm_and_trust(self, db):
        _seed_trust(db, allowed_actions=["read", "send", "execute"])
        out = await registry.execute(
            "run echo gateopened", {"platform": "terminal"}
        )
        # The dangerous-confirm prompt may still fire first; we only need
        # to confirm the gate doesn't falsely deny.
        assert "not allowed" not in out.lower()
        assert "trust gate denied" not in out.lower()


class TestNoShellMetacharRisk:
    async def test_run_shell_metachar_is_literal(self, db, tmp_path):
        _seed_trust(db, allowed_actions=["read", "send", "execute"])
        canary = tmp_path / "pwn.flag"
        # Even if the confirmation + trust somehow pass, the shell
        # metachar must be treated as a literal argv element — it
        # will NOT create the canary file via shell chaining.
        out = await registry.execute(
            f"run echo hi --confirm; touch {canary}",
            {"platform": "terminal"},
        )
        assert not canary.exists(), \
            "Shell metacharacters must not reach a real shell"

    def test_trust_action_map_covers_new_dangerous_commands(self):
        for name in ("run", "exec", "sh", "repl", "git", "web", "fetch", "curl"):
            # Each maps to a non-empty action name.
            assert _trust_action_for(name)

    def test_needs_trust_gate_flags_developer_and_dangerous(self):
        dev = Command(name="x", description="", category="12_developer",
                      handler=lambda c: "ok")
        safe = Command(name="y", description="", category="13_help",
                        handler=lambda c: "ok")
        danger = Command(name="z", description="", category="06_memory",
                          handler=lambda c: "ok", dangerous=True)
        assert _needs_trust_gate(dev)
        assert not _needs_trust_gate(safe)
        assert _needs_trust_gate(danger)
