"""Regressions for the nuclear reset / panic button.

The grandma contract:
  - One thing to type, anywhere, from any state.
  - Bot acknowledges within ~1s.
  - Bot is back fresh in ~30s with long-term memory intact.
  - The phrase match must be PRE-LLM, PRE-DB, PRE-tool dispatch so
    even an agent in a weird state still hears it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.channels.telegram_bot import (
    TelegramChannel,
    _is_panic_message,
)


# ── Phrase-recognition unit tests ──────────────────────────────────


class TestPanicRecognition:
    @pytest.mark.parametrize("phrase", [
        "/reset",
        "/panic",
        "/nuclear",
        "🆘",
        "/RESET",        # case-insensitive
        "  /reset  ",    # whitespace-tolerant
    ])
    def test_exact_commands_recognized(self, phrase):
        assert _is_panic_message(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "reset my agent",
        "Reset my agent please",
        "nuclear reset",
        "factory reset",
        "bring my agent back",
        "bring back my agent",
        "my agent is broken",
        "my bot is broken",
        "agent is stuck",
        "bot is stuck",
        "Hey, my bot is stuck on something — can you help?",
    ])
    def test_phrase_matches_anywhere_in_message(self, phrase):
        assert _is_panic_message(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "/reset my password",       # ambiguous slash command
        "I need to reset something",
        "what is a nuclear reactor",
        "factory of cars",
        "the agent is great",
        "tell me about reset",
    ])
    def test_unrelated_messages_do_not_trigger(self, phrase):
        assert _is_panic_message(phrase) is False

    def test_none_and_empty(self):
        assert _is_panic_message(None) is False
        assert _is_panic_message("") is False
        assert _is_panic_message("   ") is False


# ── End-to-end handler behavior ────────────────────────────────────


def _make_channel() -> TelegramChannel:
    return TelegramChannel(allowed_user_ids=["1"])


@pytest.mark.asyncio
async def test_panic_message_short_circuits_before_agent():
    """The whole point: panic check runs FIRST. on_message must
    never be invoked when the panic phrase fires — that's how we
    survive an agent in a weird state."""
    ch = _make_channel()
    on_message = AsyncMock(return_value="this should never be called")
    ch.on_message = on_message  # type: ignore[assignment]

    update = SimpleNamespace(message=SimpleNamespace(
        text="/reset",
        from_user=SimpleNamespace(id=1, first_name="Grandma"),
        chat_id=42,
        reply_text=AsyncMock(),
    ))

    with patch.object(
        ch, "_trigger_self_restart", new=AsyncMock(),
    ) as fake_restart:
        await ch._handle(update, None)

    on_message.assert_not_called()       # agent never reached
    update.message.reply_text.assert_called_once()  # but user got the ack
    fake_restart.assert_called_once()     # restart was scheduled


@pytest.mark.asyncio
async def test_panic_ack_uses_sanitizer():
    """Even the panic-ack message goes through the universal
    sanitizer, so a malformed reply template can't blow up."""
    ch = _make_channel()
    update = SimpleNamespace(message=SimpleNamespace(
        text="🆘",
        from_user=SimpleNamespace(id=1, first_name="g"),
        chat_id=42,
        reply_text=AsyncMock(),
    ))
    with patch.object(ch, "_trigger_self_restart", new=AsyncMock()):
        await ch._handle(update, None)

    sent_text = update.message.reply_text.call_args.args[0]
    assert "Resetting" in sent_text
    assert "memory" in sent_text.lower()  # reassures grandma
    assert len(sent_text) <= 4096          # sanitizer ceiling


@pytest.mark.asyncio
async def test_unauthorized_panic_dropped():
    """Unauthorized senders cannot panic-restart someone else's bot."""
    ch = TelegramChannel(allowed_user_ids=["999"])  # only allow 999
    on_message = AsyncMock()
    ch.on_message = on_message  # type: ignore[assignment]
    update = SimpleNamespace(message=SimpleNamespace(
        text="/reset",
        from_user=SimpleNamespace(id=42, first_name="Mallory"),
        chat_id=99,
        reply_text=AsyncMock(),
    ))
    with patch.object(ch, "_trigger_self_restart", new=AsyncMock()) as fake_restart:
        await ch._handle(update, None)

    on_message.assert_not_called()
    update.message.reply_text.assert_not_called()
    fake_restart.assert_not_called()


@pytest.mark.asyncio
async def test_panic_reply_failure_still_schedules_restart():
    """If reply_text raises (e.g. flood control), we MUST still
    schedule the restart — grandma's reset can't be blocked by a
    transient Telegram-side issue."""
    ch = _make_channel()
    update = SimpleNamespace(message=SimpleNamespace(
        text="reset my agent",
        from_user=SimpleNamespace(id=1, first_name="g"),
        chat_id=42,
        reply_text=AsyncMock(side_effect=RuntimeError("telegram 429")),
    ))
    with patch.object(ch, "_trigger_self_restart", new=AsyncMock()) as fake_restart:
        await ch._handle(update, None)

    fake_restart.assert_called_once()
