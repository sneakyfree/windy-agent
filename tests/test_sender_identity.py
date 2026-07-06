"""Sender identity → band enforcement (Sprint 4, 2026-07-04 audit;
TOFU owner binding, 2026-07-06 Windy 0 fix).

The public-launch disqualifier: only telegram checked who was talking;
every other channel ran every sender at Band.OWNER. These tests pin
the contract end to end:

- allowlist configured → strangers are SANDBOX (chat yes; commands,
  rescue, legacy tools no);
- nothing configured → **Trust-On-First-Use**: the first sender is
  bound as owner and persisted; every later sender is a SANDBOX
  stranger. This is the grandma-safe default that replaced the old
  everyone-is-OWNER legacy mode (which greeted a demo user by the
  owner's name and offered them SSH/fleet tooling);
- WINDY_LEGACY_OWNER_MODE=1 → explicit opt-in to the old everyone-is-
  OWNER behavior, with a loud warning.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from windyfly.agent.capabilities import Band
from windyfly.channels import identity
from windyfly.channels.base import IncomingMessage, handle_incoming
from windyfly.channels.manager import ChannelManager


@pytest.fixture(autouse=True)
def _fresh_warnings(monkeypatch, tmp_path):
    # Isolate the TOFU bindings file to a per-test temp path so tests
    # never read or pollute the real ~/.windy/owner-bindings.json and
    # each test starts with a clean (unbound) slate.
    monkeypatch.setenv(
        "WINDY_OWNER_BINDINGS_PATH", str(tmp_path / "owner-bindings.json")
    )
    identity._reset_warnings_for_tests()
    monkeypatch.delenv("WINDY_OWNER_IDS", raising=False)
    monkeypatch.delenv("AGENT_OWNER_TELEGRAM_ID", raising=False)
    monkeypatch.delenv("WINDY_LEGACY_OWNER_MODE", raising=False)
    yield
    identity._reset_warnings_for_tests()


class TestResolveBand:
    def test_owner_match(self, monkeypatch):
        monkeypatch.setenv("WINDY_OWNER_IDS", "discord:111,slack:U9")
        assert identity.resolve_band("discord", "111") == Band.OWNER
        assert identity.resolve_band("slack", "U9") == Band.OWNER

    def test_stranger_is_sandbox_when_allowlist_configured(self, monkeypatch):
        monkeypatch.setenv("WINDY_OWNER_IDS", "discord:111")
        assert identity.resolve_band("discord", "999") == Band.SANDBOX
        assert identity.resolve_band("discord", None) == Band.SANDBOX

    def test_telegram_env_absorbed(self, monkeypatch):
        monkeypatch.setenv("AGENT_OWNER_TELEGRAM_ID", "8545")
        assert identity.resolve_band("telegram", "8545") == Band.OWNER
        assert identity.resolve_band("telegram", "666") == Band.SANDBOX

    def test_guest_mode_caps_at_user(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_OWNER_IDS", "discord:111")
        flag = tmp_path / ".guest"
        flag.write_text("on")
        monkeypatch.setenv("WINDY_GUEST_FLAG", str(flag))
        assert identity.resolve_band("discord", "111") == Band.USER

    def test_config_trust_section_merged(self):
        band = identity.resolve_band(
            "signal", "+1555", config={"trust": {"owner_ids": ["signal:+1555"]}},
        )
        assert band == Band.OWNER


class TestTrustOnFirstUse:
    """Default posture: no allowlist configured → bind the first
    sender, sandbox everyone after. This is the grandma-safe default."""

    def test_first_sender_becomes_owner(self):
        assert identity.resolve_band("matrix", "@grandma:hs") == Band.OWNER

    def test_later_stranger_is_sandbox(self):
        # Grandma speaks first → bound. A stranger who finds the agent
        # afterwards is NEVER treated as owner.
        assert identity.resolve_band("matrix", "@grandma:hs") == Band.OWNER
        assert identity.resolve_band("matrix", "@stranger:hs") == Band.SANDBOX
        # The real owner still resolves to OWNER on subsequent messages.
        assert identity.resolve_band("matrix", "@grandma:hs") == Band.OWNER

    def test_binding_persists_to_disk(self, monkeypatch, tmp_path):
        path = tmp_path / "bindings.json"
        monkeypatch.setenv("WINDY_OWNER_BINDINGS_PATH", str(path))
        identity.resolve_band("discord", "owner-1")
        assert path.exists()
        # A fresh process (cleared in-memory guards) still recognizes
        # the persisted owner and sandboxes a newcomer.
        identity._reset_warnings_for_tests()
        assert identity.resolve_band("discord", "owner-1") == Band.OWNER
        assert identity.resolve_band("discord", "newcomer") == Band.SANDBOX

    def test_tofu_binds_per_platform(self):
        # Binding one platform must not silently claim another.
        assert identity.resolve_band("discord", "d-owner") == Band.OWNER
        assert identity.resolve_band("slack", "s-owner") == Band.OWNER
        assert identity.resolve_band("discord", "s-owner") == Band.SANDBOX

    def test_no_sender_when_unconfigured_is_local_operator(self):
        # No owner configured AND no sender id = a local / unattributed
        # context (CLI, embedded) — a remote channel always carries a
        # sender, so this can only be the operator. Resolves to OWNER,
        # and does NOT bind a bogus empty owner, so a real sender can
        # still claim ownership afterwards.
        assert identity.resolve_band("matrix", None) == Band.OWNER
        assert identity.resolve_band("matrix", "@grandma:hs") == Band.OWNER
        # But once an allowlist is configured, a no-sender message is a
        # stranger — the locked-down agent refuses it. (Covered fully by
        # TestResolveBand.test_stranger_is_sandbox_when_allowlist_configured.)

    def test_tofu_warns_once(self, caplog):
        with caplog.at_level(logging.WARNING, logger="windyfly.channels.identity"):
            identity.resolve_band("irc", "a")
            identity.resolve_band("irc", "b")
        assert sum(
            "Trust-On-First-Use" in r.message for r in caplog.records
        ) == 1

    def test_explicit_env_is_honored_over_binding(self, monkeypatch):
        # A stale binding never overrides an explicit allowlist: setting
        # WINDY_OWNER_IDS is always sufficient to keep the real owner
        # recognized and to lock the agent's policy down.
        identity.resolve_band("discord", "auto-bound")  # binds auto-bound
        monkeypatch.setenv("WINDY_OWNER_IDS", "discord:real-owner")
        assert identity.resolve_band("discord", "real-owner") == Band.OWNER

    def test_bind_owner_helper(self):
        identity.bind_owner("matrix", "@boss:hs")
        assert identity.resolve_band("matrix", "@boss:hs") == Band.OWNER
        assert identity.resolve_band("matrix", "@rando:hs") == Band.SANDBOX


class TestLegacyOwnerMode:
    """Explicit opt-in to the old everyone-is-OWNER behavior."""

    def test_legacy_mode_treats_all_as_owner(self, monkeypatch):
        monkeypatch.setenv("WINDY_LEGACY_OWNER_MODE", "1")
        assert identity.resolve_band("discord", "anyone") == Band.OWNER
        assert identity.resolve_band("discord", "someone-else") == Band.OWNER

    def test_legacy_mode_warns_once(self, monkeypatch, caplog):
        monkeypatch.setenv("WINDY_LEGACY_OWNER_MODE", "1")
        with caplog.at_level(logging.WARNING, logger="windyfly.channels.identity"):
            identity.resolve_band("discord", "a")
            identity.resolve_band("discord", "b")
        assert sum(
            "LEGACY OWNER MODE" in r.message for r in caplog.records
        ) == 1

    def test_explicit_allowlist_beats_legacy_flag(self, monkeypatch):
        # If both are set, the allowlist wins for the configured platform
        # (strangers are sandboxed) — legacy only covers unconfigured
        # platforms.
        monkeypatch.setenv("WINDY_LEGACY_OWNER_MODE", "1")
        monkeypatch.setenv("WINDY_OWNER_IDS", "discord:111")
        assert identity.resolve_band("discord", "999") == Band.SANDBOX
        assert identity.resolve_band("discord", "111") == Band.OWNER


class TestManagerPassthrough:
    def _msg(self, sender="999"):
        return IncomingMessage(
            platform="discord", channel_id="c1", sender_id=sender,
            sender_name="x", text="hello",
        )

    def test_band_passed_to_band_aware_callback(self, monkeypatch):
        monkeypatch.setenv("WINDY_OWNER_IDS", "discord:111")
        seen = {}

        async def respond(text, session_id, band=None):
            seen["band"] = band
            return "ok"

        mgr = ChannelManager(respond)
        asyncio.run(mgr._handle_message(self._msg(sender="999")))
        assert seen["band"] == Band.SANDBOX
        asyncio.run(mgr._handle_message(self._msg(sender="111")))
        assert seen["band"] == Band.OWNER

    def test_two_arg_callback_still_works(self):
        async def respond(text, session_id):
            return "legacy ok"

        mgr = ChannelManager(respond)
        result = asyncio.run(mgr._handle_message(self._msg()))
        assert result == "legacy ok"


class TestCommandGating:
    def _handle(self, text, sender):
        return asyncio.run(handle_incoming(text, {
            "platform": "discord", "channel_id": "c1", "sender_id": sender,
        }))

    def test_stranger_blocked_from_commands(self, monkeypatch):
        monkeypatch.setenv("WINDY_OWNER_IDS", "discord:111")
        was_cmd, reply = self._handle("/status", "999")
        assert was_cmd
        assert "owner-only" in reply

    def test_stranger_blocked_from_rescue(self, monkeypatch):
        monkeypatch.setenv("WINDY_OWNER_IDS", "discord:111")
        was_cmd, reply = self._handle("/pause", "999")
        assert was_cmd
        assert "Only my owner" in reply
        from windyfly.agent.spend_monitor import is_paused
        assert not is_paused()  # side effect must NOT have fired

    def test_stranger_can_still_chat(self, monkeypatch):
        monkeypatch.setenv("WINDY_OWNER_IDS", "discord:111")
        was_cmd, _ = self._handle("hello, what's the weather?", "999")
        assert not was_cmd  # falls through to the (sandboxed) agent

    def test_owner_keeps_rescue(self, monkeypatch):
        monkeypatch.setenv("WINDY_OWNER_IDS", "discord:111")
        was_cmd, reply = self._handle("/pause", "111")
        assert was_cmd
        assert "Paused" in reply
        self._handle("/resume", "111")

    def test_tofu_first_sender_keeps_rescue(self):
        # No allowlist → first sender is the TOFU owner → rescue works.
        was_cmd, reply = self._handle("/pause", "first-caller")
        assert was_cmd
        assert "Paused" in reply
        self._handle("/resume", "first-caller")

    def test_tofu_later_stranger_blocked_from_rescue(self):
        # First caller binds; a later stranger is sandboxed and blocked.
        self._handle("hi", "first-caller")  # binds owner
        was_cmd, reply = self._handle("/pause", "second-caller")
        assert was_cmd
        assert "Only my owner" in reply
        from windyfly.agent.spend_monitor import is_paused
        assert not is_paused()


class TestSandboxToolExclusion:
    def test_legacy_tools_hidden_from_sandbox_band(self):
        """The loop must not offer ungated legacy tools (sms, mail,
        cloud) to SANDBOX senders. Verified at the source level via
        the band gate constant — the full loop path is exercised by
        the channel e2e suites."""
        import inspect
        from windyfly.agent import loop
        src = inspect.getsource(loop.agent_respond)
        assert "band <= Band.SANDBOX" in src
        assert "legacy_tools = []" in src
