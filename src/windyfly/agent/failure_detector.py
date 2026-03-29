"""Friction and failure detection — the \"Never Wrong Twice\" system.

Detects when the user corrects the agent, logs failures with fault type
classification, and checks for recurring failures to trigger proactive behavior.
"""

from __future__ import annotations

import re
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.failures import check_recurring_failure, get_recent_failures, log_failure
from windyfly.memory.write_queue import Priority, WriteQueue

# Pattern → fault_type classification
# Uses negative lookahead to avoid false positives on agreement phrases
FRICTION_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)^(?!.*(no\s+(problem|worries|thanks|that'?s\s+(fine|great|good|perfect|right|correct)))).*\b(no,?\s+(?:that'?s\s+)?(?:wrong|incorrect|not\s+(?:right|what|correct)))\b", "factual_error"),
    (r"(?i)\b(what i meant was|let me clarify|i didn'?t mean)\b", "ambiguity_mishandled"),
    (r"(?i)\b(i (?:already )?(?:said|told you|asked you|meant))\b(?!.*(?:thanks|great|perfect))", "preference_miss"),
    (r"(?i)\b(try again|redo|retry|one more time|do it (?:again|over))\b", "execution_failure"),
]


def detect_friction(
    user_message: str,
    previous_agent_message: str | None = None,
) -> dict[str, Any] | None:
    """Detect friction patterns in a user message.

    Checks the user's message against known correction/frustration patterns
    to identify when the agent made a mistake.

    Args:
        user_message: The user's current message.
        previous_agent_message: The agent's last response (for context).

    Returns:
        Friction dict if detected, None otherwise.
    """
    # Short messages are rarely corrections — avoid false positives
    if len(user_message.strip()) < 10:
        return None

    for pattern, fault_type in FRICTION_PATTERNS:
        if re.search(pattern, user_message):
            return {
                "fault_type": fault_type,
                "user_message": user_message,
                "agent_message": previous_agent_message or "",
                "pattern_matched": pattern,
            }
    return None


def handle_friction(
    db: Database,
    write_queue: WriteQueue,
    friction: dict[str, Any],
) -> str | None:
    """Log a detected friction event and check for recurrence.

    Args:
        db: Database instance.
        write_queue: WriteQueue for async writes.
        friction: Friction dict from detect_friction().

    Returns:
        Extra system prompt instruction if this is a recurring failure,
        or a basic correction prompt, or None.
    """
    fault_type = friction["fault_type"]
    description = f"User: {friction['user_message'][:200]} | Agent: {friction['agent_message'][:200]}"

    # Log the failure (HIGH priority)
    write_queue.enqueue(
        Priority.HIGH,
        log_failure,
        db,
        fault_type,
        description,
    )

    # Log event for observability (G12)
    from windyfly.observability.events import log_event
    log_event(db, write_queue, "failure.detect", {
        "fault_type": fault_type,
        "pattern": friction.get("pattern_matched", ""),
    })

    # Check for recurring failure
    is_recurring = check_recurring_failure(db, fault_type, description)

    if is_recurring:
        # Fetch specific failure history so the agent knows what to avoid
        recent = get_recent_failures(db, fault_type=fault_type, limit=3)
        history = " | ".join(
            f.get("description", "")[:80] for f in recent
        ) if recent else ""
        return (
            f"⚠️ RECURRING ISSUE ({fault_type}): The user has corrected you on "
            f"similar issues recently. Recent examples: {history[:300]}. "
            "Be extra careful and precise. Double-check your response before sending."
        )
    else:
        return (
            "The user just corrected you. Acknowledge the correction gracefully, "
            "review what went wrong, and provide the right answer."
        )
