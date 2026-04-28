"""Regressions for chunked reply + typing-indicator UX.

Two grandma-tour wins:

  1. Long replies (LLM produces 50K characters) get split at
     paragraph/sentence boundaries and sent as multiple messages,
     so grandma sees the WHOLE thing instead of a "[truncated]"
     marker on a hard cut.

  2. While the agent is thinking, the bot keeps Telegram's
     typing-indicator alive. Grandma never sees a 10-second
     unresponsive gap — she sees "Windy Fly is typing…" and waits.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.channels.telegram_bot import (
    TelegramChannel,
    _REPLY_CHUNK_SIZE,
    _TYPING_REFRESH_S,
)
from windyfly.observability.sanitize import split_for_telegram


def _make_channel() -> TelegramChannel:
    return TelegramChannel(allowed_user_ids=["1"])


# ── Pure splitter (no I/O) ─────────────────────────────────────────


class TestSplitter:
    def test_short_text_one_chunk(self):
        assert split_for_telegram("hello world") == ["hello world"]

    def test_empty_text_no_chunks(self):
        assert split_for_telegram("") == []

    def test_exactly_at_limit_one_chunk(self):
        text = "x" * 4000
        chunks = split_for_telegram(text, max_chunk=4000)
        assert len(chunks) == 1
        assert len(chunks[0]) == 4000

    def test_two_chunks_split_at_paragraph(self):
        para1 = "Hello dear. " * 200  # ~2400 chars
        para2 = "And another paragraph. " * 200  # ~4600 chars
        text = para1 + "\n\n" + para2
        chunks = split_for_telegram(text, max_chunk=3000)
        assert len(chunks) >= 2
        # First chunk should end at the paragraph break (no leading
        # text from para2)
        assert "And another" not in chunks[0]
        # All chunks under the ceiling
        assert all(len(c) <= 3000 for c in chunks)

    def test_chunks_split_at_sentence_when_no_paragraph(self):
        """No paragraph break; should split at sentence end."""
        text = "Sentence one. " * 500  # ~7000 chars, all one paragraph
        chunks = split_for_telegram(text, max_chunk=2000)
        assert len(chunks) >= 4
        # Each chunk should END at a sentence boundary
        for c in chunks[:-1]:  # last chunk may be a partial
            assert c.rstrip().endswith(".")

    def test_pathological_no_breaks_hard_cut(self):
        """No newlines, no periods, no spaces — must still chunk."""
        text = "x" * 10_000
        chunks = split_for_telegram(text, max_chunk=4000)
        assert len(chunks) >= 3
        assert all(len(c) <= 4000 for c in chunks)
        # No content lost
        assert "".join(chunks).count("x") == 10_000

    def test_giant_input_does_not_explode(self):
        text = ("Short paragraph.\n\n" * 5000)
        chunks = split_for_telegram(text, max_chunk=4000)
        assert all(len(c) <= 4000 for c in chunks)
        # Reassembled length is reasonably close to input (joiner whitespace lost)
        assert sum(len(c) for c in chunks) >= len(text) * 0.95


# ── _send_long_reply integration ────────────────────────────────────


class TestSendLongReply:
    @pytest.mark.asyncio
    async def test_short_reply_one_call(self):
        ch = _make_channel()
        message = SimpleNamespace(reply_text=AsyncMock())
        await ch._send_long_reply(message, "Hi grandma!")
        assert message.reply_text.call_count == 1
        sent = message.reply_text.call_args.args[0]
        assert "grandma" in sent.lower()

    @pytest.mark.asyncio
    async def test_long_reply_multiple_calls_no_truncation_marker(self):
        ch = _make_channel()
        message = SimpleNamespace(reply_text=AsyncMock())
        big = "Sentence here. " * 800  # ~12.8K chars
        await ch._send_long_reply(message, big)
        assert message.reply_text.call_count >= 2
        # Across all chunks the truncation marker MUST NOT appear —
        # we chunk, we don't truncate.
        all_sent = "".join(
            call.args[0] for call in message.reply_text.call_args_list
        )
        assert "[truncated]" not in all_sent

    @pytest.mark.asyncio
    async def test_chunk_size_under_telegram_limit(self):
        ch = _make_channel()
        message = SimpleNamespace(reply_text=AsyncMock())
        big = "x" * 50_000
        await ch._send_long_reply(message, big)
        for call in message.reply_text.call_args_list:
            assert len(call.args[0]) <= 4096  # Telegram hard limit

    @pytest.mark.asyncio
    async def test_send_failure_does_not_abort_remaining_chunks(self):
        """If chunk #1 fails (e.g. transient flood control), we still
        try to send chunks #2, #3 — grandma gets as much as we can."""
        ch = _make_channel()
        attempts = {"n": 0}

        async def flaky_reply(text):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("telegram 429 transient")
            return None

        message = SimpleNamespace(reply_text=flaky_reply)
        big = "Para.\n\n" * 2000  # forces many chunks
        await ch._send_long_reply(message, big)
        # Multiple chunks attempted despite the first failure.
        assert attempts["n"] >= 2

    @pytest.mark.asyncio
    async def test_none_reply_uses_polite_fallback(self):
        ch = _make_channel()
        message = SimpleNamespace(reply_text=AsyncMock())
        await ch._send_long_reply(message, None)
        assert message.reply_text.call_count == 1
        sent = message.reply_text.call_args.args[0]
        assert "try again" in sent.lower()


# ── Typing indicator ────────────────────────────────────────────────


class TestTypingIndicator:
    @pytest.mark.asyncio
    async def test_keep_typing_refreshes_until_cancelled(self):
        ch = _make_channel()
        send_chat_action = AsyncMock()
        ch._app = SimpleNamespace(
            bot=SimpleNamespace(send_chat_action=send_chat_action),
        )

        # Speed the refresh interval to 1ms so a real test runs fast.
        # Patching the constant is safer than patching asyncio.sleep
        # (which leads to recursion when fast_sleep itself uses sleep).
        with patch(
            "windyfly.channels.telegram_bot._TYPING_REFRESH_S", 0.001,
        ):
            task = asyncio.create_task(ch._keep_typing(chat_id=42))
            await asyncio.sleep(0.05)  # let several iterations happen
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert send_chat_action.call_count >= 1
        # Each call should pass action="typing"
        for call in send_chat_action.call_args_list:
            assert call.kwargs.get("action") == "typing"

    @pytest.mark.asyncio
    async def test_keep_typing_no_app_returns_quietly(self):
        ch = _make_channel()
        ch._app = None
        await ch._keep_typing(chat_id=42)  # should return without raising

    @pytest.mark.asyncio
    async def test_keep_typing_swallows_send_chat_action_errors(self):
        """If send_chat_action raises (transient Telegram issue), the
        typing loop must NOT die — it's cosmetic."""
        ch = _make_channel()
        attempts = {"n": 0}

        async def flaky(**kwargs):
            attempts["n"] += 1
            raise RuntimeError("transient")

        ch._app = SimpleNamespace(bot=SimpleNamespace(send_chat_action=flaky))

        with patch(
            "windyfly.channels.telegram_bot._TYPING_REFRESH_S", 0.001,
        ):
            task = asyncio.create_task(ch._keep_typing(chat_id=42))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Multiple attempts despite each one raising — loop kept ticking.
        assert attempts["n"] >= 2
