"""Tone from an import, as slider values (strand P2.21.5, P2.21.12).

``communication_style`` reads a ChatGPT owner's own messages (``author.role == "user"`` only; the
assistant side is never passed in) and turns plain counts into humour, formality and verbosity on the
0-10 slider scale. It is deterministic: no model call, and no text leaves the host or is kept, only the
three numbers.

``tone_values`` turns any parser's ``personality`` dict into the slider values an owner can apply
(``/soul apply``), dropping anything that is not a number from 0 to 10.
"""

from __future__ import annotations

import math
import re
import statistics
from typing import Any

TONE_SLIDERS = ("humor", "formality", "verbosity")
MIN_MESSAGES = 20          # fewer than this says too little about how someone writes

_CONTRACTION = re.compile(r"\b\w+'(?:s|re|ve|ll|d|m|t)\b|\b(?:gonna|wanna|gotta|kinda|dunno|ya|u|ur)\b",
                          re.IGNORECASE)
_POLITE = re.compile(r"\b(?:please|thank you|thanks|kindly|would you|could you|dear|regards)\b", re.IGNORECASE)
_LAUGH = re.compile(r"\b(?:lol|lmao|rofl|haha\w*|hehe\w*|jk)\b|[\U0001F600-\U0001F64F\U0001F923\U0001F602]",
                    re.IGNORECASE)


def _clamp(x: float) -> int:
    return max(0, min(10, round(x)))


def communication_style(messages: list[str]) -> dict[str, int]:
    """Humour, formality and verbosity (0-10) from how the owner writes; {} when there is too little."""
    texts = [m.strip() for m in messages if isinstance(m, str) and m.strip()]
    if len(texts) < MIN_MESSAGES:
        return {}
    n = len(texts)
    words = statistics.median(len(t.split()) for t in texts)
    casual = sum(1 for t in texts if _CONTRACTION.search(t) or t[0].islower()) / n
    polite = sum(1 for t in texts if _POLITE.search(t)) / n
    laugh = sum(1 for t in texts if _LAUGH.search(t)) / n
    # verbosity: a median of 5 words or fewer is 2; each doubling adds 2 (10, 20, 40, 80+ words).
    verbosity = 2 if words <= 5 else _clamp(2 + 2 * math.log2(words / 5))
    return {
        "humor": _clamp(3 + 14 * laugh),             # never laughs: 3; a laugh in half the messages: 10
        "formality": _clamp(5 + 6 * polite - 5 * casual),
        "verbosity": verbosity,
    }


def tone_values(personality: dict[str, Any] | None) -> dict[str, int]:
    """The slider values in an import's personality: whole numbers 0-10 for the tone sliders only."""
    out: dict[str, int] = {}
    for name in TONE_SLIDERS:
        v = (personality or {}).get(name)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if 0 <= v <= 10:
            out[name] = int(round(v))
    return out
