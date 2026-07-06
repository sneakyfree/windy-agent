"""Launchable-channel wiring (2026-07-06).

Signal, IRC, and Teams had complete adapter classes but no `--channel`
dispatch entry, so they were unreachable. These pin: (1) they're now in
the launch list (and WhatsApp deliberately is NOT — it's the Matrix-
bridge gateway, not an in-agent adapter); (2) Teams' proactive send() no
longer silently drops — it delivers via a captured conversation
reference or raises, so the manager never believes a dropped send
succeeded.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from windyfly.channels.base import OutgoingMessage
from windyfly.channels.teams_bot import TeamsChannel
from windyfly.main import CHANNEL_CHOICES


class TestChannelChoices:
    def test_orphaned_adapters_now_launchable(self):
        for ch in ("signal", "irc", "teams"):
            assert ch in CHANNEL_CHOICES, f"{ch} should be launchable"

    def test_existing_channels_preserved(self):
        for ch in ("cli", "matrix", "sms", "telegram", "discord", "slack"):
            assert ch in CHANNEL_CHOICES

    def test_whatsapp_is_not_an_in_agent_channel(self):
        # WhatsApp is served by the Windy-run Matrix-bridge gateway, not an
        # in-agent adapter — wiring `--channel whatsapp` would contradict
        # the locked gateway design.
        assert "whatsapp" not in CHANNEL_CHOICES


class TestTeamsProactiveSend:
    async def test_send_before_start_raises(self):
        ch = TeamsChannel()
        with pytest.raises(RuntimeError, match="not started"):
            await ch.send(OutgoingMessage(text="hi", channel_id="c1"))

    async def test_send_without_reference_raises_not_silent(self):
        # This is the fix: the old code logged + dropped. Now a proactive
        # send with no captured conversation reference is a loud error.
        ch = TeamsChannel()
        ch._adapter_bf = object()  # pretend started
        ch._app_id = "app-123"
        with pytest.raises(RuntimeError, match="conversation reference"):
            await ch.send(OutgoingMessage(text="hi", channel_id="unknown"))

    async def test_send_delivers_via_continue_conversation(self):
        ch = TeamsChannel()
        bf = AsyncMock()
        ch._adapter_bf = bf
        ch._app_id = "app-123"
        ch._conv_refs["c1"] = {"ref": "for-c1"}

        await ch.send(OutgoingMessage(text="proactive hello", channel_id="c1"))

        assert bf.continue_conversation.await_count == 1
        args, _ = bf.continue_conversation.call_args
        assert args[0] == {"ref": "for-c1"}  # the captured reference
        assert args[2] == "app-123"          # app id
        # the middle arg is the send-turn callback
        assert callable(args[1])

    async def test_send_falls_back_to_last_conversation(self):
        ch = TeamsChannel()
        bf = AsyncMock()
        ch._adapter_bf = bf
        ch._app_id = "app-123"
        ch._conv_refs["c9"] = {"ref": "for-c9"}
        ch._last_conv_id = "c9"

        # No explicit channel_id → uses the most-recent inbound conversation.
        await ch.send(OutgoingMessage(text="ping", channel_id=""))
        args, _ = bf.continue_conversation.call_args
        assert args[0] == {"ref": "for-c9"}
