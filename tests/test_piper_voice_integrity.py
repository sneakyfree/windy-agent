"""Voice replies failed SILENTLY for four months. Two bugs, stacked.

On Windy 0, 2026-05-14 → 2026-09-13:

  1. The cached ``en_US-amy-medium.onnx`` was a truncated download —
     32,768,000 bytes of an expected ~63 MB, cut at a round block
     boundary. ``_load_voice`` only checked ``onnx.exists()``, so the
     bad file was never replaced and every load died with
     "INVALID_PROTOBUF: Protobuf parsing failed".

  2. Behind it, ``synthesize`` passed a bare ``io.BytesIO`` to
     ``voice.synthesize_wav``, which needs a ``wave.Wave_write`` — it
     calls ``.setframerate()`` on the object. That raised
     AttributeError, which the function's own ``except`` swallowed
     into "no voice reply".

Neither ever surfaced to the user: the channel adapter treats None as
"send text only". The agent just quietly stopped talking. These tests
pin both so it cannot happen silently again.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from windyfly.voice import piper


# ── Bug 1: a truncated model must not be cached forever ───────────


class TestCorruptModelSelfHeals:
    """A load attempt is the integrity test.

    Note a size floor would NOT have caught the real incident: the
    truncated file was 32,768,000 bytes, which looks entirely plausible
    next to a ~63 MB real model. Only trying to load it tells the truth.
    """

    @pytest.fixture
    def cached(self, tmp_path, monkeypatch):
        name = "en_US-amy-medium"
        # Contents are irrelevant — only loadability is tested.
        (tmp_path / f"{name}.onnx").write_bytes(b"\0" * 1024)
        (tmp_path / f"{name}.onnx.json").write_text("{}")
        monkeypatch.setattr(piper, "_VOICE", None)
        monkeypatch.setattr(piper, "is_available", lambda: True)
        monkeypatch.setattr(piper, "voice_name", lambda: name)
        monkeypatch.setattr(piper, "_voice_cache_dir", lambda: tmp_path)
        return name, tmp_path

    def test_corrupt_model_is_deleted_and_redownloaded(self, cached, monkeypatch):
        """The regression itself: an unloadable cached model must be
        replaced, not returned to the caller forever."""
        name, tmp_path = cached
        good = object()
        attempts = []

        def fake_load(onnx, cfg):
            attempts.append(onnx)
            if len(attempts) == 1:
                raise RuntimeError("INVALID_PROTOBUF : Protobuf parsing failed")
            return good

        monkeypatch.setattr(piper, "_try_load", fake_load)
        with patch.object(piper, "_attempt_download", return_value=True) as dl:
            assert piper._load_voice() is good

        dl.assert_called_once()
        assert len(attempts) == 2, "must retry the load after re-downloading"

    def test_corrupt_model_removed_from_disk_before_redownload(self, cached, monkeypatch):
        name, tmp_path = cached
        onnx = tmp_path / f"{name}.onnx"
        seen = {}

        monkeypatch.setattr(
            piper, "_try_load",
            MagicMock(side_effect=RuntimeError("Protobuf parsing failed")),
        )

        def record_download(voice, cache_dir):
            seen["existed_at_download"] = onnx.exists()
            return False

        monkeypatch.setattr(piper, "_attempt_download", record_download)
        assert piper._load_voice() is None
        assert seen["existed_at_download"] is False, (
            "the corrupt file must be deleted before re-downloading, or the "
            "downloader may skip it as already present"
        )

    def test_still_broken_after_redownload_returns_none(self, cached, monkeypatch):
        monkeypatch.setattr(
            piper, "_try_load",
            MagicMock(side_effect=RuntimeError("Protobuf parsing failed")),
        )
        with patch.object(piper, "_attempt_download", return_value=True):
            assert piper._load_voice() is None

    def test_healthy_model_loads_without_redownloading(self, cached, monkeypatch):
        good = object()
        monkeypatch.setattr(piper, "_try_load", lambda onnx, cfg: good)
        with patch.object(piper, "_attempt_download") as dl:
            assert piper._load_voice() is good
        dl.assert_not_called()


# ── Bug 2: synthesize must hand piper a wave.Wave_write ───────────


class TestSynthesizeWavHandoff:

    def test_synthesize_wav_receives_a_wave_write_not_a_bytesio(self, monkeypatch):
        """Passing BytesIO raised
        "'_io.BytesIO' object has no attribute 'setframerate'".
        """
        seen = {}

        class FakeVoice:
            def synthesize_wav(self, text, wav_file, **kw):
                seen["type"] = type(wav_file).__name__
                # Prove the object supports the calls piper really makes.
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(22050)
                wav_file.writeframes(b"\0\0" * 2205)

        monkeypatch.setattr(piper, "_load_voice", lambda: FakeVoice())
        out = piper.synthesize("hello")

        assert seen["type"] == "Wave_write", (
            "piper.synthesize_wav must be handed a wave.Wave_write; a bare "
            "BytesIO raises AttributeError on .setframerate()"
        )
        assert out, "should return WAV bytes"
        # And what comes back is a real, parseable WAV.
        w = wave.open(io.BytesIO(out))
        assert w.getframerate() == 22050
        assert w.getnchannels() == 1

    def test_empty_audio_returns_none_not_empty_bytes(self, monkeypatch):
        """A voice that writes no frames is a failure, not a 0-byte
        'success' for the channel adapter to try to send."""
        class SilentVoice:
            def synthesize_wav(self, text, wav_file, **kw):
                return None

        monkeypatch.setattr(piper, "_load_voice", lambda: SilentVoice())
        # A wave file with no frames still has a 44-byte header, so this
        # asserts the real contract: never return something unusable.
        out = piper.synthesize("hello")
        assert out is None or len(out) > 44

    def test_synthesis_exception_is_swallowed_to_none(self, monkeypatch):
        """Voice must never crash a turn — text reply still goes out."""
        class BrokenVoice:
            def synthesize_wav(self, text, wav_file, **kw):
                raise RuntimeError("onnx exploded")

        monkeypatch.setattr(piper, "_load_voice", lambda: BrokenVoice())
        assert piper.synthesize("hello") is None

    def test_no_voice_loaded_returns_none(self, monkeypatch):
        monkeypatch.setattr(piper, "_load_voice", lambda: None)
        assert piper.synthesize("hello") is None

    @pytest.mark.parametrize("text", ["", "   ", "\n"])
    def test_empty_text_returns_none(self, text):
        assert piper.synthesize(text) is None
