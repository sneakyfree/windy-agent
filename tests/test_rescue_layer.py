"""Channel-agnostic rescue layer (Sprint 2, 2026-07-04 audit).

Before this layer, /pause, /resurrect, /normal, /lifeboat, /spend,
/auto-resurrect, and the /reset panic button existed ONLY on telegram —
a wedged agent on Discord/Slack/Matrix/Signal/Teams/IRC/WhatsApp had no
escape hatch. These tests pin the rescue contract end-to-end through
``handle_incoming`` (the shared path every non-telegram channel uses).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from windyfly.channels.base import handle_incoming
from windyfly.channels.rescue import try_rescue


def _handle(text: str, platform: str = "discord"):
    return asyncio.run(
        handle_incoming(text, {"platform": platform, "channel_id": "c1"})
    )


class TestPauseResume:
    def test_pause_writes_flag_and_acks(self):
        was_cmd, reply = _handle("/pause")
        assert was_cmd
        assert "Paused" in reply
        from windyfly.agent.spend_monitor import is_paused
        assert is_paused()

    def test_resume_clears_flag(self):
        _handle("/pause")
        was_cmd, reply = _handle("/resume")
        assert was_cmd
        assert "Awake" in reply
        from windyfly.agent.spend_monitor import is_paused
        assert not is_paused()

    def test_resume_when_not_paused_is_friendly(self):
        was_cmd, reply = _handle("/resume")
        assert was_cmd
        assert "wasn't paused" in reply


class TestLifeboat:
    def test_lifeboat_is_readonly_status(self):
        """/lifeboat reports state; it must NOT toggle resurrection
        (safer ordering than a toggle for a grandma poking around)."""
        was_cmd, reply = _handle("/lifeboat")
        assert was_cmd
        from windyfly.agent.resurrect import is_resurrected
        assert not is_resurrected()

    def test_normal_when_not_resurrected(self):
        was_cmd, reply = _handle("/normal")
        assert was_cmd
        assert "wasn't in lifeboat" in reply

    def test_resurrect_without_ollama_gives_install_hint(self):
        # conftest mocks is_ollama_available → False
        was_cmd, reply = _handle("/resurrect")
        assert was_cmd
        assert "Ollama" in reply

    def test_auto_resurrect_toggle_round_trip(self):
        was_cmd, reply = _handle("/auto-resurrect off")
        assert was_cmd and "OFF" in reply
        from windyfly.agent.resurrect import is_auto_resurrect_disabled
        assert is_auto_resurrect_disabled()
        was_cmd, reply = _handle("/auto-resurrect on")
        assert was_cmd and "ON" in reply
        assert not is_auto_resurrect_disabled()


class TestPanic:
    def test_reset_schedules_restart_and_acks(self):
        with patch("windyfly.channels.rescue.schedule_restart") as mock_rs:
            was_cmd, reply = _handle("/reset", platform="slack")
        assert was_cmd
        assert "restart" in reply.lower() or "reset" in reply.lower()
        mock_rs.assert_called_once()

    def test_grandma_phrase_triggers_panic(self):
        with patch("windyfly.channels.rescue.schedule_restart") as mock_rs:
            was_cmd, reply = _handle("help, my bot is broken!!")
        assert was_cmd
        mock_rs.assert_called_once()

    def test_panic_clears_lifeboat_flag(self):
        from windyfly.agent import resurrect as r
        # Force the resurrect flag on directly (file-flag primitive).
        flag = r._flag_path()
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text('{"active": true, "model": "llama3.2:3b"}')
        with patch("windyfly.channels.rescue.schedule_restart"):
            _handle("/reset")
        assert not r.is_resurrected()


class TestLayering:
    def test_non_rescue_command_falls_through_to_registry(self):
        # /ping must reach the registry (rescue must not swallow it).
        # In this bare context the registry is unloaded, so the proof
        # of fall-through is the registry's own unknown-command reply
        # naming the command.
        was_cmd, reply = _handle("/ping")
        assert was_cmd
        # Loaded registry answers "Pong"; bare registry answers
        # "Unknown command: ping". Either proves rescue passed it on.
        assert "pong" in reply.lower() or "ping" in reply.lower()

    def test_plain_chat_is_not_a_command(self):
        was_cmd, reply = _handle("hello there, how are you?")
        assert not was_cmd

    def test_try_rescue_returns_none_for_non_rescue(self):
        assert try_rescue("/ping", platform="discord") is None
        assert try_rescue("", platform="discord") is None
        assert try_rescue(None, platform="discord") is None
