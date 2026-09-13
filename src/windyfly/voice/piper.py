"""Voice synthesis (text → speech) via local Piper TTS.

Voice-OUT counterpart to ``whisper.py`` (voice-IN, PR #129). Same
opt-in graceful-degradation pattern:

  - ``is_available()`` probes piper-tts once and caches.
  - ``synthesize(text)`` returns WAV bytes or None on any failure
    (deps absent / model not downloaded / voice unavailable / text
    too long). The channel adapter handles None by sending only a
    text reply — never crashes mid-conversation.
  - Lazy model load — first call downloads weights (~63MB for
    en_US-amy-medium) to ``~/.cache/piper/`` if not present.
  - Single global model — model instantiation is 2-5s on cold
    cache; can't pay that per call.

Why Piper over alternatives:
  - Truly offline (no API key, no rate limit, no cloud cost)
  - Quality is genuinely good — natural female English voice
    that lands acceptably for grandma demos
  - ~63MB model fits a phone app's worth of memory
  - Real-time synthesis on CPU (no GPU needed)
  - Permissive license

Default voice: ``en_US-amy-medium`` — natural, warm, female,
American English. Override via ``WINDY_PIPER_VOICE``. Pre-download
once on the host with::

    python -m piper.download_voices en_US-amy-medium

Telegram voice-note format expectation (OGG/Opus) is NOT handled
here — this module returns WAV bytes; the channel adapter is
responsible for ffmpeg conversion or falling back to send_audio.
"""

from __future__ import annotations

import io
import logging
import os
import wave
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Cap synthesized text length to avoid 5-minute voice notes when
# the LLM produces a long answer. ~1000 chars ≈ ~90 seconds of
# speech at Piper's default rate. Beyond that the user is reading
# the text reply anyway.
MAX_SYNTH_CHARS = 1000


_VOICE: Any = None
_AVAILABLE: bool | None = None


def is_available() -> bool:
    """True iff piper-tts is importable. Caches first probe."""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    try:
        import piper  # noqa: F401
        _AVAILABLE = True
    except ImportError:
        _AVAILABLE = False
        logger.debug(
            "piper-tts not installed; voice synthesis disabled. "
            "Install with: pip install windyfly[voice]"
        )
    return _AVAILABLE


def voice_name() -> str:
    return os.environ.get("WINDY_PIPER_VOICE", "en_US-amy-medium")


def _voice_cache_dir() -> Path:
    return Path(os.environ.get(
        "WINDY_PIPER_CACHE",
        os.path.expanduser("~/.cache/piper"),
    ))


def _load_voice() -> Any:
    """Lazy-load the configured Piper voice. Returns None on any
    failure (deps absent, model file missing/truncated, malformed
    config)."""
    global _VOICE
    if not is_available():
        return None
    if _VOICE is not None:
        return _VOICE
    name = voice_name()
    cache_dir = _voice_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    onnx = cache_dir / f"{name}.onnx"
    cfg = cache_dir / f"{name}.onnx.json"

    # piper provides a download utility but the API path varies by
    # version; use whichever entry-point this install offers.
    if not (onnx.exists() and cfg.exists()):
        if not _attempt_download(name, cache_dir):
            logger.warning(
                "Piper voice %s not cached at %s and download failed. "
                "Pre-download with: python -m piper.download_voices %s",
                name, cache_dir, name,
            )
            return None

    # "Present" is not "usable". Windy 0 cached a TRUNCATED
    # en_US-amy-medium.onnx (32,768,000 bytes of an expected ~63 MB — a
    # download cut at a round block boundary) on 2026-05-14. The old
    # check was `onnx.exists()`, so the bad file was never replaced:
    # every load died with "INVALID_PROTOBUF: Protobuf parsing failed"
    # and voice replies were silently dead for four months.
    #
    # A load attempt is the only honest integrity test — a size floor
    # would not have caught this one (32 MB looks plausible), and real
    # voices range from ~20 MB (x_low) to ~63 MB (medium), so there is
    # no threshold that is both safe and maintainable. So: try to load,
    # and if that fails, treat the cached file as corrupt, delete it,
    # and re-download ONCE. A partial file is worse than no file —
    # no file self-heals, a partial one never does.
    _VOICE = _load_or_repair(name, onnx, cfg, cache_dir)
    return _VOICE


def _try_load(onnx: Path, cfg: Path) -> Any:
    """Load the model, or return None if it cannot be parsed."""
    from piper.voice import PiperVoice
    return PiperVoice.load(str(onnx), str(cfg))


def _load_or_repair(name: str, onnx: Path, cfg: Path, cache_dir: Path) -> Any:
    """Load the voice; on a corrupt cache, re-download once and retry."""
    try:
        logger.info("Loading Piper voice %s from %s", name, onnx)
        return _try_load(onnx, cfg)
    except Exception as e:
        logger.warning(
            "Piper voice %s failed to load (%s) — treating the cached "
            "model as corrupt and re-downloading once", name, e,
        )

    try:
        onnx.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Could not remove corrupt Piper model %s: %s", onnx, e)
        return None

    if not _attempt_download(name, cache_dir):
        logger.warning(
            "Piper voice %s was corrupt and re-download failed. "
            "Fix with: python -m piper.download_voices %s", name, name,
        )
        return None

    try:
        return _try_load(onnx, cfg)
    except Exception as e:
        logger.warning(
            "Piper voice %s still unloadable after re-download: %s", name, e,
        )
        return None


def _attempt_download(voice: str, cache_dir: Path) -> bool:
    """Try the documented download paths; return True on success."""
    try:
        # Newer piper-tts versions expose download_voices as a
        # callable from the module.
        from piper import download_voices
        download_voices.download_voice(voice, cache_dir)
        return True
    except Exception:
        pass
    try:
        import subprocess
        import sys
        # Fall back to the CLI module path that ships with the package.
        # Use sys.executable so we invoke the SAME interpreter that's
        # running the bot — hardcoded "python" exits 127 on systems
        # whose venv only has python3 on PATH (no python symlink).
        # Surfaced 2026-05-14 on Windy 0 when the primary import path
        # failed → fallback shelled out to "python" → 127 → the parent
        # bot process exited with the child's status → systemd
        # marked the service deactivating → outage.
        result = subprocess.run(
            [sys.executable, "-m", "piper.download_voices", voice,
             "--data-dir", str(cache_dir)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return True
        logger.debug("piper.download_voices CLI exit %s: %s",
                     result.returncode, result.stderr[:200])
    except Exception as e:
        logger.debug("piper download via CLI failed: %s", e)
    return False


def synthesize(text: str) -> bytes | None:
    """Synthesize ``text`` to WAV bytes.

    Returns None when:
      - piper-tts not installed
      - model can't be loaded (not downloaded, file corrupt)
      - text is empty
      - any synthesis exception

    Caller (channel adapter) treats None as "no voice reply, send
    text only" — never crashes.

    Long text is truncated to ``MAX_SYNTH_CHARS`` to keep voice
    notes under ~90 seconds. The user still sees the full text
    reply alongside.
    """
    if not text or not text.strip():
        return None

    voice = _load_voice()
    if voice is None:
        return None

    truncated = text[:MAX_SYNTH_CHARS]
    if len(text) > MAX_SYNTH_CHARS:
        # Cut at last sentence end if we can, to avoid mid-word stop
        cut_at = max(
            truncated.rfind(". "),
            truncated.rfind("! "),
            truncated.rfind("? "),
        )
        if cut_at > MAX_SYNTH_CHARS // 2:
            truncated = truncated[:cut_at + 1]

    try:
        buf = io.BytesIO()
        # Version-dependent API:
        #   - synthesize_wav(text, wave.Wave_write) — current piper.
        #     It calls .setframerate()/.setsampwidth() on the object,
        #     so a bare BytesIO raises AttributeError. Passing one was
        #     the bug: this except swallowed it into "no voice reply",
        #     so voice output failed SILENTLY on every turn.
        #   - synthesize(text, wav_io) — older piper wrote straight to
        #     a binary buffer. (In current piper `synthesize` is a
        #     generator of AudioChunks and takes no file argument, so
        #     it is only correct on those older installs.)
        if hasattr(voice, "synthesize_wav"):
            with wave.open(buf, "wb") as wav_file:
                voice.synthesize_wav(truncated, wav_file)
        else:
            voice.synthesize(truncated, buf)
        data = buf.getvalue()
        if not data:
            logger.warning("Piper produced no audio for %d chars", len(truncated))
            return None
        return data
    except Exception as e:
        logger.warning("Piper synthesize failed: %s", e)
        return None
