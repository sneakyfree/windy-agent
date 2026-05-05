"""Voice-message handler regression tests.

Pin the contract added in PR #129:

  - Voice messages no longer silently drop. Three cases:
    1. faster-whisper not installed → polite text reply explaining
       how to type instead. NEVER silent drop.
    2. transcription returns empty string → polite "couldn't make
       out the words" reply. NEVER silent drop.
    3. transcription succeeds → dispatch through the same agent
       pipeline as text, send text reply prefixed with "🎙 Heard:"
       so the user can verify what the bot understood.
  - Voice files are deleted after transcription regardless of
    outcome (no audio accumulates on disk).
  - Auth gate fires on voice path same as text path: messages from
    non-allowlisted senders are silently dropped (consistent with
    the existing fleet allowFrom convention).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.channels.base import OutgoingMessage
from windyfly.channels.telegram_bot import TelegramChannel
from windyfly.voice import whisper as _transcribe_module


def _make_channel(allowed_user_ids=None):
    chan = TelegramChannel(allowed_user_ids=allowed_user_ids or ["1001"])
    chan.on_message = AsyncMock(return_value=OutgoingMessage(text="ok", channel_id="999"))
    chan._send_long_reply = AsyncMock()
    chan._keep_typing = AsyncMock()
    return chan


def _make_voice_update(sender_id="1001", file_id="voicefile-x"):
    upd = MagicMock()
    upd.message.from_user.id = int(sender_id)
    upd.message.from_user.first_name = "TestUser"
    upd.message.chat_id = 999
    voice = MagicMock()
    voice.file_id = file_id
    upd.message.voice = voice
    upd.message.audio = None
    upd.message.text = None
    return upd


def _make_context_with_download(temp_audio_path: str):
    """Build a python-telegram-bot context whose get_file().download_to_drive
    writes a non-empty file to the path we hand it. Use a tiny WAV
    header so the downstream check (file.exists() and st_size > 0)
    passes."""
    ctx = MagicMock()
    file_obj = MagicMock()

    async def _download(path):
        # Write something so the file exists with non-zero size.
        with open(path, "wb") as f:
            f.write(b"RIFF\x24\x00\x00\x00WAVEfmt \x10")
        return path

    file_obj.download_to_drive = AsyncMock(side_effect=_download)
    ctx.bot.get_file = AsyncMock(return_value=file_obj)
    return ctx


# ── Path 1: no transcription stack ─────────────────────────────────


@pytest.mark.asyncio
async def test_voice_handler_replies_when_whisper_unavailable():
    """No faster-whisper → polite text reply, NEVER silent drop."""
    chan = _make_channel()
    upd = _make_voice_update()
    ctx = MagicMock()

    with patch.object(_transcribe_module, "_AVAILABLE", False):
        await chan._handle_voice(upd, ctx)

    # The polite-fallback reply MUST have been sent.
    chan._send_long_reply.assert_awaited_once()
    sent_text = chan._send_long_reply.await_args.args[1]
    assert "voice support isn't installed" in sent_text.lower() or \
           "voice support is" in sent_text.lower()
    # And the agent dispatch must NOT have fired (no LLM call burned
    # for an unprocessable message).
    chan.on_message.assert_not_called()


# ── Path 2: empty transcript ───────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_handler_replies_when_transcript_empty(tmp_path):
    """Whisper returns None / empty → polite "couldn't make out"
    reply, NEVER silent drop."""
    chan = _make_channel()
    upd = _make_voice_update()
    ctx = _make_context_with_download(str(tmp_path / "voice.ogg"))

    with patch.object(_transcribe_module, "_AVAILABLE", True), \
         patch("windyfly.voice.whisper.transcribe", return_value=None), \
         patch("windyfly.voice.transcribe", return_value=None):
        await chan._handle_voice(upd, ctx)

    chan._send_long_reply.assert_awaited_once()
    sent_text = chan._send_long_reply.await_args.args[1]
    assert "couldn't make out" in sent_text.lower() or \
           "could you try again" in sent_text.lower()
    chan.on_message.assert_not_called()


# ── Path 3: full success flow ──────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_handler_dispatches_transcript_to_agent(tmp_path):
    """Successful transcription → IncomingMessage with the transcript
    flows to on_message; reply includes 'Heard:' confirmation."""
    chan = _make_channel()
    upd = _make_voice_update()
    ctx = _make_context_with_download(str(tmp_path / "voice.ogg"))

    with patch.object(_transcribe_module, "_AVAILABLE", True), \
         patch("windyfly.voice.whisper.transcribe", return_value="hello bot how are you"), \
         patch("windyfly.voice.transcribe", return_value="hello bot how are you"):
        await chan._handle_voice(upd, ctx)

    # Agent dispatch fired with the transcript.
    chan.on_message.assert_awaited_once()
    incoming = chan.on_message.await_args.args[0]
    assert incoming.text == "hello bot how are you"
    assert incoming.sender_id == "1001"

    # Reply was sent and includes the "Heard:" confirmation prefix.
    chan._send_long_reply.assert_awaited_once()
    sent_text = chan._send_long_reply.await_args.args[1]
    assert "heard:" in sent_text.lower()
    assert "hello bot how are you" in sent_text


# ── File cleanup ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_handler_deletes_temp_file_on_success(tmp_path):
    """The downloaded audio must be removed after transcription so
    /tmp doesn't accumulate every grandma's recordings."""
    chan = _make_channel()
    upd = _make_voice_update()
    ctx = _make_context_with_download(str(tmp_path / "voice.ogg"))

    seen_paths: list[str] = []

    def fake_transcribe(path):
        seen_paths.append(str(path))
        return "test transcript"

    with patch.object(_transcribe_module, "_AVAILABLE", True), \
         patch("windyfly.voice.whisper.transcribe", side_effect=fake_transcribe), \
         patch("windyfly.voice.transcribe", side_effect=fake_transcribe):
        await chan._handle_voice(upd, ctx)

    assert seen_paths, "transcribe should have been called with a path"
    # The path passed to transcribe must NOT exist after the handler
    # returns.
    for p in seen_paths:
        assert not os.path.exists(p), (
            f"voice tempfile {p} leaked — must be cleaned up "
            "regardless of transcription success"
        )


@pytest.mark.asyncio
async def test_voice_handler_deletes_temp_file_on_empty_transcript(tmp_path):
    """Even when transcription returns nothing, the audio file must
    still be deleted — no leakage on the failure path either."""
    chan = _make_channel()
    upd = _make_voice_update()
    ctx = _make_context_with_download(str(tmp_path / "voice.ogg"))

    seen_paths: list[str] = []

    def fake_transcribe(path):
        seen_paths.append(str(path))
        return None

    with patch.object(_transcribe_module, "_AVAILABLE", True), \
         patch("windyfly.voice.whisper.transcribe", side_effect=fake_transcribe), \
         patch("windyfly.voice.transcribe", side_effect=fake_transcribe):
        await chan._handle_voice(upd, ctx)

    assert seen_paths
    for p in seen_paths:
        assert not os.path.exists(p), (
            f"voice tempfile {p} leaked on the failure path"
        )


# ── Auth gate ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_handler_drops_unauthorized_sender():
    """Allowlist applies to voice path same as text. Unauthorized
    sender gets silent drop — consistent with text path's behavior."""
    chan = _make_channel(allowed_user_ids=["999"])  # Grant's id
    upd = _make_voice_update(sender_id="424242")  # NOT allowed
    ctx = MagicMock()

    await chan._handle_voice(upd, ctx)

    chan._send_long_reply.assert_not_called()
    chan.on_message.assert_not_called()


# ── transcribe module sanity ───────────────────────────────────────


def test_transcribe_returns_none_on_missing_path(tmp_path):
    """A nonexistent path must NOT crash — must return None so the
    channel adapter can surface the polite reply."""
    bogus = tmp_path / "does-not-exist.ogg"
    assert _transcribe_module.transcribe(bogus) is None


def test_transcribe_returns_none_on_empty_file(tmp_path):
    """A zero-byte file must return None — Whisper would crash on
    empty audio; we short-circuit."""
    empty = tmp_path / "empty.ogg"
    empty.write_bytes(b"")
    assert _transcribe_module.transcribe(empty) is None


def test_transcribe_returns_none_when_unavailable(tmp_path):
    """Module-level fast path: if faster-whisper isn't importable,
    transcribe() returns None without trying anything else."""
    fakefile = tmp_path / "x.ogg"
    fakefile.write_bytes(b"some audio bytes")
    with patch.object(_transcribe_module, "_AVAILABLE", False):
        assert _transcribe_module.transcribe(fakefile) is None


def test_is_available_caches_result():
    """is_available() must cache to avoid the import-attempt cost
    on every call. This matters when the channel adapter checks it
    on every voice message."""
    # Force unknown state, probe, ensure cached
    with patch.object(_transcribe_module, "_AVAILABLE", None):
        result1 = _transcribe_module.is_available()
        # _AVAILABLE is now set; subsequent call should not re-probe
        cached = _transcribe_module._AVAILABLE
    assert result1 == cached


def test_model_name_default_and_override(monkeypatch):
    """Default model is tiny.en (small, cheap, English-only).
    WINDY_WHISPER_MODEL env var overrides for higher accuracy."""
    monkeypatch.delenv("WINDY_WHISPER_MODEL", raising=False)
    assert _transcribe_module.model_name() == "tiny.en"
    monkeypatch.setenv("WINDY_WHISPER_MODEL", "small.en")
    assert _transcribe_module.model_name() == "small.en"
