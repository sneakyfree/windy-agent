"""Intent detector — extract goals from user messages.

Uses pattern matching (Phase 3) and LLM analysis (future).
"""

from __future__ import annotations

import re
from typing import Any


# Goal/intent keywords
INTENT_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)i (want|need|would like|'d like) to (.+?)(?:\.|!|\?|$)", "user_said"),
    (r"(?i)i('m| am) (trying|planning|hoping|going) to (.+?)(?:\.|!|\?|$)", "user_said"),
    (r"(?i)(can you|could you|would you) (help me|assist me|) ?(.+?)(?:\.|!|\?|$)", "user_said"),
    (r"(?i)my goal is (.+?)(?:\.|!|\?|$)", "user_said"),
    (r"(?i)i need (.+?)(?:\.|!|\?|$)", "user_said"),
    (r"(?i)remind me to (.+?)(?:\.|!|\?|$)", "user_said"),
]


def detect_intent(
    user_message: str,
    context: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Detect if a user message expresses a goal or intent.

    Args:
        user_message: The user's message.
        context: Optional conversation context (for future LLM analysis).

    Returns:
        Dict with has_intent, description, origin — or None.
    """
    for pattern, origin in INTENT_PATTERNS:
        match = re.search(pattern, user_message)
        if match:
            # Extract the most meaningful group
            groups = match.groups()
            description = groups[-1].strip() if groups else user_message
            if len(description) > 5:
                return {
                    "has_intent": True,
                    "description": description,
                    "origin": origin,
                }

    return None
