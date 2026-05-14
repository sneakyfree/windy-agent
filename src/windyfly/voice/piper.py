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
    failure (deps absent, model file missing, malformed config)."""
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

    # If model isn't cached, attempt download. piper provides a
    # download utility but the API path varies by version; use
    # whichever entry-point this install offers.
    if not (onnx.exists() and cfg.exists()):
        downloaded = _attempt_download(name, cache_dir)
        if not downloaded:
            logger.warning(
                "Piper voice %s not cached at %s and download failed. "
                "Pre-download with: python -m piper.download_voices %s",
                name, cache_dir, name,
            )
            return None

    try:
        from piper.voice import PiperVoice
        logger.info("Loading Piper voice %s from %s", name, onnx)
        _VOICE = PiperVoice.load(str(onnx), str(cfg))
        return _VOICE
    except Exception as e:
        logger.warning("Failed to load Piper voice %s: %s", name, e)
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
        # Piper's synthesize() writes WAV bytes to a file-like
        # object. Different version APIs:
        #   - voice.synthesize(text, wav_io) — older
        #   - voice.synthesize_wav(text, wav_io) — newer
        # Try newer first, fall back gracefully.
        if hasattr(voice, "synthesize_wav"):
            voice.synthesize_wav(truncated, buf)
        else:
            voice.synthesize(truncated, buf)
        return buf.getvalue()
    except Exception as e:
        logger.warning("Piper synthesize failed: %s", e)
        return None
