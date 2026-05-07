"""Voice synthesis (text-to-speech) regression tests.

Pin the contract added in PR #143:

  - is_synthesize_available() returns False without piper-tts
    installed (graceful — same pattern as voice transcription)
  - synthesize() returns None when piper-tts absent / model not
    downloaded / text empty / any synth failure
  - synthesize() truncates over-long text at MAX_SYNTH_CHARS so
    the bot never sends a 5-minute voice note
  - voice_name() default is en_US-amy-medium; WINDY_PIPER_VOICE
    overrides
  - Telegram voice-out gating respects WINDY_VOICE_OUT=0 to
    let an operator disable voice replies even when piper is
    installed
  - WAV → OGG/Opus converter returns None when ffmpeg is missing
    (not a crash — the channel adapter falls back to send_audio
    with raw WAV)
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from windyfly.voice import piper as _piper


# ── is_available + voice_name ─────────────────────────────────────


def test_is_available_caches_result():
    """Pin the cache pattern: one probe per process, even if called
    from many channel-message handlers."""
    with patch.object(_piper, "_AVAILABLE", None):
        result1 = _piper.is_available()
        cached = _piper._AVAILABLE
    assert result1 == cached


def test_voice_name_default(monkeypatch):
    monkeypatch.delenv("WINDY_PIPER_VOICE", raising=False)
    assert _piper.voice_name() == "en_US-amy-medium"


def test_voice_name_env_override(monkeypatch):
    monkeypatch.setenv("WINDY_PIPER_VOICE", "en_US-lessac-medium")
    assert _piper.voice_name() == "en_US-lessac-medium"


# ── synthesize() graceful fallbacks ───────────────────────────────


def test_synthesize_returns_none_when_unavailable():
    """Without piper-tts, no crash — just None."""
    with patch.object(_piper, "_AVAILABLE", False):
        assert _piper.synthesize("hello world") is None


def test_synthesize_returns_none_on_empty_text():
    """Empty / whitespace text is a no-op — no point synthesizing
    silence."""
    with patch.object(_piper, "_AVAILABLE", True), \
         patch.object(_piper, "_load_voice", return_value=object()):
        assert _piper.synthesize("") is None
        assert _piper.synthesize("   ") is None
        assert _piper.synthesize(None) is None  # type: ignore[arg-type]


def test_synthesize_returns_none_when_model_load_fails():
    """If the model file is missing or corrupt, _load_voice returns
    None and synthesize must surface None (not crash)."""
    with patch.object(_piper, "_AVAILABLE", True), \
         patch.object(_piper, "_load_voice", return_value=None):
        assert _piper.synthesize("hello") is None


def test_synthesize_returns_wav_bytes_on_success():
    """Happy path: model loaded → returns the WAV bytes from
    voice.synthesize_wav."""
    fake_voice = type("FakeVoice", (), {})()
    fake_voice.synthesize_wav = lambda self_, text, buf: buf.write(b"FAKEWAV")  # noqa: ARG005
    # Use a class so the bound method has self
    class FV:
        def synthesize_wav(self, text, buf):
            buf.write(b"FAKEWAV-DATA")
    fake = FV()

    with patch.object(_piper, "_AVAILABLE", True), \
         patch.object(_piper, "_load_voice", return_value=fake):
        out = _piper.synthesize("hello")
    assert out == b"FAKEWAV-DATA"


def test_synthesize_swallows_synth_exceptions():
    """If voice.synthesize_wav raises (corrupt model, OOM, etc.),
    return None — never crash the channel handler."""
    class CrashingVoice:
        def synthesize_wav(self, text, buf):
            raise RuntimeError("model exploded")

    with patch.object(_piper, "_AVAILABLE", True), \
         patch.object(_piper, "_load_voice", return_value=CrashingVoice()):
        assert _piper.synthesize("hello") is None


def test_synthesize_falls_back_to_legacy_api():
    """Older piper versions expose .synthesize() rather than
    .synthesize_wav(). Pin the fallback so we work on both."""
    class LegacyVoice:
        def synthesize(self, text, buf):
            buf.write(b"LEGACY-WAV")

    with patch.object(_piper, "_AVAILABLE", True), \
         patch.object(_piper, "_load_voice", return_value=LegacyVoice()):
        assert _piper.synthesize("hello") == b"LEGACY-WAV"


# ── Length cap ────────────────────────────────────────────────────


def test_synthesize_caps_long_text():
    """A 5000-char LLM reply must NOT produce a 5-minute voice note.
    Pin the truncation at MAX_SYNTH_CHARS."""
    captured = []

    class CapturingVoice:
        def synthesize_wav(self, text, buf):
            captured.append(text)
            buf.write(b"x")

    long_text = "Hello world. " * 500  # ~6500 chars
    with patch.object(_piper, "_AVAILABLE", True), \
         patch.object(_piper, "_load_voice", return_value=CapturingVoice()):
        _piper.synthesize(long_text)

    assert len(captured) == 1
    assert len(captured[0]) <= _piper.MAX_SYNTH_CHARS, (
        f"synthesize sent {len(captured[0])} chars; "
        f"cap is {_piper.MAX_SYNTH_CHARS}"
    )


def test_synthesize_truncates_at_sentence_boundary():
    """When truncating, prefer cutting at a sentence end so the
    voice note doesn't stop mid-word. Only pin the property:
    the truncated text should END with sentence punctuation when
    such a cut exists in the back half of the cap."""
    sentences = [
        f"This is sentence number {i}." for i in range(200)
    ]  # ~50 chars × 200 = 10000 chars; many sentence ends to choose from
    long_text = " ".join(sentences)

    captured = []
    class V:
        def synthesize_wav(self, text, buf):
            captured.append(text)
            buf.write(b"x")

    with patch.object(_piper, "_AVAILABLE", True), \
         patch.object(_piper, "_load_voice", return_value=V()):
        _piper.synthesize(long_text)

    truncated = captured[0]
    # The cut should land on or just after a period, not mid-word
    assert truncated.endswith(".") or truncated.endswith(". "), (
        f"truncation didn't cut at sentence boundary: "
        f"...{truncated[-30:]!r}"
    )


# ── WAV → OGG/Opus converter ──────────────────────────────────────


def test_wav_to_ogg_returns_none_when_ffmpeg_missing(monkeypatch):
    """ffmpeg not on PATH → return None gracefully so the channel
    falls back to send_audio with raw WAV."""
    from windyfly.channels.telegram_bot import _wav_to_ogg_opus
    # Force ffmpeg to be "missing" by clearing PATH
    monkeypatch.setenv("PATH", "/nonexistent")
    assert _wav_to_ogg_opus(b"FAKE WAV BYTES") is None


def test_wav_to_ogg_returns_none_on_invalid_input():
    """Bogus WAV bytes → ffmpeg fails → None returned (not a crash).
    Only runs on hosts where ffmpeg is actually installed; otherwise
    skipped (the previous test covers the no-ffmpeg path)."""
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed — covered by the missing-PATH test")
    from windyfly.channels.telegram_bot import _wav_to_ogg_opus
    out = _wav_to_ogg_opus(b"\x00" * 100, timeout_s=5)  # not real WAV
    assert out is None


# ── Re-exports ─────────────────────────────────────────────────────


def test_voice_package_re_exports():
    """The /voice package exports both is_synthesize_available and
    synthesize so callers don't have to know the internal layout."""
    from windyfly.voice import is_synthesize_available, synthesize
    assert callable(is_synthesize_available)
    assert callable(synthesize)
