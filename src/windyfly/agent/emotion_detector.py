"""Emotional awareness — detect user emotional state.

Pattern-based detection for stress, excitement, and neutral states.
Tracks emotional trends over conversation sessions.
"""

from __future__ import annotations

import re
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.episodes import get_recent_episodes

STRESS_SIGNALS: list[str] = [
    r"(?i)(ugh|frustrated|annoying|this is (broken|stupid)|wtf|ffs)",
    r"(?i)(i('m| am) (stressed|tired|exhausted|overwhelmed))",
    r"[A-Z]{5,}",   # ALL CAPS (shouting)
    r"(!{3,})",       # Multiple exclamation marks
]

EXCITEMENT_SIGNALS: list[str] = [
    r"(?i)(awesome|amazing|perfect|love it|yes!|great|wow)",
    r"(?i)(this is (great|incredible|perfect))",
]


def detect_emotional_context(message: str) -> str:
    """Detect the emotional context of a message.

    Args:
        message: The user's message.

    Returns:
        'stressed', 'excited', or 'neutral'.
    """
    for pattern in STRESS_SIGNALS:
        if re.search(pattern, message):
            return "stressed"

    for pattern in EXCITEMENT_SIGNALS:
        if re.search(pattern, message):
            return "excited"

    return "neutral"


def get_emotional_trend(
    db: Database,
    session_id: str,
    window: int = 5,
) -> str:
    """Get the emotional trend over the last N episodes in a session.

    Args:
        db: Database instance.
        session_id: Current session ID.
        window: Number of recent episodes to analyze.

    Returns:
        'sustained_stress', 'excited', or 'neutral'.
    """
    recent = get_recent_episodes(db, limit=window, session_id=session_id)

    emotional_counts: dict[str, int] = {"stressed": 0, "excited": 0, "neutral": 0}
    consecutive_stressed = 0
    max_consecutive = 0

    for ep in recent:
        context = ep.get("emotional_context", "neutral") or "neutral"
        emotional_counts[context] = emotional_counts.get(context, 0) + 1

        if context == "stressed":
            consecutive_stressed += 1
            max_consecutive = max(max_consecutive, consecutive_stressed)
        else:
            consecutive_stressed = 0

    # 3+ consecutive stressed episodes = sustained stress
    if max_consecutive >= 3:
        return "sustained_stress"

    # Return majority emotion
    if emotional_counts.get("excited", 0) > len(recent) / 2:
        return "excited"

    return "neutral"
