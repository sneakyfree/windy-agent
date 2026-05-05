"""Voice → text via local Whisper.

Same opt-in graceful-degradation pattern as ``memory/embeddings.py``:

  - ``is_available()`` probes faster-whisper once and caches.
  - ``transcribe(audio_path)`` returns the transcript or None on
    any failure (deps absent / model load failed / audio garbled /
    ffmpeg missing). The channel adapter handles None by sending a
    polite "I couldn't make out the words" reply instead of crashing.
  - Lazy model load — first call costs the model-download time
    (typically 75MB tiny.en, cached at ~/.cache/huggingface/hub).
    Subsequent calls reuse the in-process model.
  - Single global model — model instantiation is 1-3s, can't pay
    that per call.

Why faster-whisper (not openai-whisper): CTranslate2-based,
~4× faster on CPU, no torch dependency. Total install ~200MB
including onnxruntime + tokenizers + huggingface-hub. Acceptable
for an opt-in feature.

Default model: ``tiny.en`` (75MB). Plenty for grandma voice notes.
Override with ``WINDY_WHISPER_MODEL=small.en`` for higher accuracy at
244MB / slower inference.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MODEL: Any = None
_AVAILABLE: bool | None = None


def is_available() -> bool:
    """True iff faster-whisper is importable. Cached after first probe."""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    try:
        import faster_whisper  # noqa: F401
        _AVAILABLE = True
    except ImportError:
        _AVAILABLE = False
        logger.debug(
            "faster-whisper not installed; voice transcription disabled. "
            "Install with: pip install windyfly[voice]"
        )
    return _AVAILABLE


def model_name() -> str:
    return os.environ.get("WINDY_WHISPER_MODEL", "tiny.en")


def _load_model() -> Any:
    global _MODEL
    if not is_available():
        return None
    if _MODEL is not None:
        return _MODEL
    name = model_name()
    try:
        from faster_whisper import WhisperModel
        # CPU + int8 quantization — fast enough for grandma voice
        # notes (typically < 30s audio) on any laptop. GPU users can
        # set WINDY_WHISPER_DEVICE=cuda.
        device = os.environ.get("WINDY_WHISPER_DEVICE", "cpu")
        compute = os.environ.get("WINDY_WHISPER_COMPUTE", "int8")
        logger.info(
            "Loading Whisper model %s (device=%s, compute=%s); "
            "first call downloads weights (~75MB for tiny.en).",
            name, device, compute,
        )
        _MODEL = WhisperModel(name, device=device, compute_type=compute)
        return _MODEL
    except Exception as e:
        logger.warning("Failed to load Whisper model %s: %s", name, e)
        return None


def transcribe(audio_path: str | Path) -> str | None:
    """Transcribe an audio file to text.

    Args:
        audio_path: Path to an audio file (any format faster-whisper /
            ffmpeg can read — Telegram voice notes are OGG/Opus and
            work fine when ffmpeg is available on the host).

    Returns:
        The transcript with leading/trailing whitespace stripped, or
        None if transcription failed for any reason. Channel adapter
        treats None as "couldn't hear" and responds politely.
    """
    if not audio_path:
        return None
    p = Path(audio_path)
    if not p.exists() or p.stat().st_size == 0:
        return None

    model = _load_model()
    if model is None:
        return None

    try:
        # beam_size=1 is fast and accurate enough for short voice
        # notes; bump if hallucination becomes a problem.
        segments, info = model.transcribe(
            str(p),
            beam_size=int(os.environ.get("WINDY_WHISPER_BEAM", "1")),
            language=os.environ.get("WINDY_WHISPER_LANG") or None,
        )
        text = " ".join(s.text for s in segments).strip()
        if not text:
            return None
        return text
    except Exception as e:
        logger.warning("transcribe() failed for %s: %s", p, e)
        return None
