"""Sender identity → band enforcement (Sprint 4, 2026-07-04 audit).

The public-launch disqualifier: only telegram checked who was talking;
every other channel ran every sender at Band.OWNER. These tests pin
the new contract end to end: allowlist configured → strangers are
SANDBOX (chat yes; commands, rescue, legacy tools no); nothing
configured → legacy OWNER with a loud warning (existing deploys keep
working).
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
def _fresh_warnings(monkeypatch):
    identity._reset_warnings_for_tests()
    monkeypatch.delenv("WINDY_OWNER_IDS", raising=False)
    monkeypatch.delenv("AGENT_OWNER_TELEGRAM_ID", raising=False)
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

    def test_unconfigured_platform_is_legacy_owner_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="windyfly.channels.identity"):
            band = identity.resolve_band("discord", "anyone")
        assert band == Band.OWNER
        assert any("NO owner allowlist" in r.message for r in caplog.records)

    def test_warning_fires_once_per_platform(self, caplog):
        with caplog.at_level(logging.WARNING, logger="windyfly.channels.identity"):
            identity.resolve_band("irc", "a")
            identity.resolve_band("irc", "b")
        assert sum(
            "NO owner allowlist" in r.message for r in caplog.records
        ) == 1

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

    def test_legacy_platform_unaffected(self):
        # No allowlist for this platform → old behavior end to end.
        was_cmd, reply = self._handle("/pause", "anyone")
        assert was_cmd
        assert "Paused" in reply
        self._handle("/resume", "anyone")


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
