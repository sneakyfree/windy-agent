"""Sender identity → band enforcement (Sprint 4, 2026-07-04 audit;
Trust-On-First-Use removed 2026-09-23, SSO #13).

The public-launch disqualifier: only telegram checked who was talking;
every other channel ran every sender at Band.OWNER. These tests pin
the contract end to end:

- allowlist configured → strangers are SANDBOX (chat yes; commands,
  rescue, legacy tools no);
- no owner known → **every remote sender is SANDBOX**. There is no
  Trust-On-First-Use: a stranger who reaches an unclaimed agent first
  must never become its owner. Owners come from an allowlist, the
  hatch pinning the owner's Matrix ID, or /pair (test_owner_pairing);
- bindings persisted earlier (hatch, pairing, legacy TOFU) stay valid;
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
    # Isolate the owner-bindings file to a per-test temp path so tests
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


class TestNoTrustOnFirstUse:
    """Default posture: no owner known → nobody remote is OWNER. The
    first sender is NOT bound (the TOFU hole SSO #13 closed)."""

    def test_first_sender_is_sandbox(self):
        assert identity.resolve_band("matrix", "@first:hs") == Band.SANDBOX

    def test_first_sender_is_not_bound(self, monkeypatch, tmp_path):
        path = tmp_path / "bindings.json"
        monkeypatch.setenv("WINDY_OWNER_BINDINGS_PATH", str(path))
        identity.resolve_band("discord", "stranger")
        assert not path.exists()
        # Still SANDBOX on every later message — nothing was learned.
        assert identity.resolve_band("discord", "stranger") == Band.SANDBOX

    def test_persisted_binding_still_owner(self, monkeypatch, tmp_path):
        # A binding written earlier (hatch, pairing, or the old TOFU path)
        # stays authoritative, so an agent that knows its owner keeps
        # knowing it after this change.
        path = tmp_path / "bindings.json"
        path.write_text('{"matrix": "@grant:hs"}', encoding="utf-8")
        monkeypatch.setenv("WINDY_OWNER_BINDINGS_PATH", str(path))
        assert identity.resolve_band("matrix", "@grant:hs") == Band.OWNER
        assert identity.resolve_band("matrix", "@stranger:hs") == Band.SANDBOX

    def test_no_sender_when_unconfigured_is_local_operator(self):
        # No owner configured AND no sender id = a local / unattributed
        # context (CLI, embedded). Unchanged: OWNER, and nothing is bound.
        assert identity.resolve_band("matrix", None) == Band.OWNER
        assert identity.resolve_band("matrix", "@grandma:hs") == Band.SANDBOX

    def test_unowned_platform_warns_once(self, caplog):
        with caplog.at_level(logging.WARNING, logger="windyfly.channels.identity"):
            identity.resolve_band("irc", "a")
            identity.resolve_band("irc", "b")
        assert sum("has no owner" in r.message for r in caplog.records) == 1

    def test_allowlist_wins(self, monkeypatch):
        monkeypatch.setenv("WINDY_OWNER_IDS", "discord:real-owner")
        assert identity.resolve_band("discord", "real-owner") == Band.OWNER
        assert identity.resolve_band("discord", "someone") == Band.SANDBOX

    def test_explicit_env_is_honored_over_binding(self, monkeypatch):
        identity.bind_owner("discord", "old-owner")
        monkeypatch.setenv("WINDY_OWNER_IDS", "discord:real-owner")
        assert identity.resolve_band("discord", "real-owner") == Band.OWNER

    def test_bind_owner_helper(self):
        identity.bind_owner("matrix", "@boss:hs")
        assert identity.resolve_band("matrix", "@boss:hs") == Band.OWNER
        assert identity.resolve_band("matrix", "@rando:hs") == Band.SANDBOX

    def test_bindings_are_per_platform(self):
        identity.bind_owner("discord", "d-owner")
        assert identity.resolve_band("discord", "d-owner") == Band.OWNER
        assert identity.resolve_band("slack", "d-owner") == Band.SANDBOX


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

    def test_unowned_first_sender_blocked_from_rescue(self):
        # No owner known → even the very first caller is a stranger.
        was_cmd, reply = self._handle("/pause", "first-caller")
        assert was_cmd
        assert "Only my owner" in reply
        from windyfly.agent.spend_monitor import is_paused
        assert not is_paused()

    def test_bound_owner_keeps_rescue(self):
        identity.bind_owner("discord", "the-owner")
        was_cmd, reply = self._handle("/pause", "the-owner")
        assert was_cmd
        assert "Paused" in reply
        self._handle("/resume", "the-owner")


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
