"""Semantic-memory embeddings — opt-in graceful-degradation layer.

The episode schema already has ``embedding BLOB`` /
``embedding_model TEXT`` / ``embedding_version INTEGER`` columns
(database.py line ~46). They were laid down for a Wave 7 vector
search that never landed. This module is the wiring.

Design rules:

  - **Fully optional.** sentence-transformers is a 600MB+ dep
    (PyTorch + transformers + model). Default install of windyfly
    stays small. ``pip install windyfly[semantic]`` opts in.
  - **Graceful fallback.** When the dep isn't present,
    ``embed(text)`` returns ``None`` and downstream callers fall
    back to FTS5-only search. No crash, no warning spam.
  - **Lazy load.** The model loads on first ``embed()`` call, not
    at import time. Imports stay fast for callers that never use
    embeddings.
  - **Single global model.** No per-call model instantiation —
    that's a 1-2s cold-start hit each call.
  - **Float32 → bytes.** Embeddings serialize to BLOB via
    numpy's tobytes(); we deserialize via frombuffer. 384 dims
    × 4 bytes = 1.5KB per episode (cheap).

Recommended model: ``all-MiniLM-L6-v2`` (80MB, 384-dim, fast on
CPU). Override via ``WINDY_EMBED_MODEL`` env var if you want a
different one.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any

logger = logging.getLogger(__name__)


_MODEL: Any = None  # cached sentence-transformer model
_MODEL_NAME: str | None = None
_AVAILABLE: bool | None = None  # tri-state cache: None=unknown, True/False after probe


def is_available() -> bool:
    """True iff sentence-transformers is importable. Caches the result
    so we don't pay the import cost more than once per process."""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    try:
        import sentence_transformers  # noqa: F401
        _AVAILABLE = True
    except ImportError:
        _AVAILABLE = False
        logger.debug(
            "sentence-transformers not installed; semantic memory "
            "disabled. Install with: pip install windyfly[semantic]"
        )
    return _AVAILABLE


def _model_name() -> str:
    return os.environ.get("WINDY_EMBED_MODEL", "all-MiniLM-L6-v2")


def _load_model() -> Any:
    """Lazy-load the model. Returns None if sentence-transformers
    isn't available."""
    global _MODEL, _MODEL_NAME
    if not is_available():
        return None
    if _MODEL is not None:
        return _MODEL
    name = _model_name()
    try:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model %s (first call; takes a moment)", name)
        _MODEL = SentenceTransformer(name)
        _MODEL_NAME = name
        return _MODEL
    except Exception as e:
        logger.warning("Failed to load embedding model %s: %s", name, e)
        return None


def model_name() -> str | None:
    """Returns the loaded model's name, or None if not loaded yet."""
    if _MODEL_NAME is not None:
        return _MODEL_NAME
    if is_available():
        return _model_name()
    return None


def embed(text: str) -> bytes | None:
    """Compute the embedding of ``text`` as serialized bytes (BLOB).

    Returns None if sentence-transformers isn't installed OR the
    model fails to load OR the input is empty. Callers must handle
    the None case — that's the graceful-fallback path.

    The return value is a numpy float32 array's ``.tobytes()`` so it
    can be stored as a SQLite BLOB. Use ``deserialize()`` to read
    back into a vector.
    """
    if not text or not text.strip():
        return None
    model = _load_model()
    if model is None:
        return None
    try:
        import numpy as np
        vec = model.encode(text, normalize_embeddings=True)
        # Force float32 for stable BLOB shape
        return np.asarray(vec, dtype=np.float32).tobytes()
    except Exception as e:
        logger.warning("embed() failed: %s", e)
        return None


def deserialize(blob: bytes | None) -> list[float] | None:
    """Read a BLOB back into a Python list of floats.

    Uses stdlib ``struct`` instead of numpy so a non-semantic install
    can still read pre-existing embedding columns (e.g., reading a
    DB written by a semantic-installed peer)."""
    if not blob:
        return None
    try:
        import struct
        # float32 = 4 bytes per float
        n = len(blob) // 4
        return list(struct.unpack(f"{n}f", blob[:n * 4]))
    except Exception:
        return None


def cosine(a: list[float] | bytes | None, b: list[float] | bytes | None) -> float:
    """Cosine similarity in [-1, 1]. Returns 0.0 if either input is
    None / empty / mismatched-length. Both embeddings come from the
    same model, so they're already L2-normalized → cosine = dot
    product. We do the math without numpy to keep this function
    callable from FTS5 SQL contexts (sqlite custom function), even
    though we use numpy elsewhere."""
    if isinstance(a, bytes):
        a = deserialize(a)
    if isinstance(b, bytes):
        b = deserialize(b)
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    # Both already normalized when produced by embed(), but tolerate
    # un-normalized inputs (e.g., from a different code path).
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
