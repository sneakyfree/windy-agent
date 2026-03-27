"""Friction and failure detection — the \"Never Wrong Twice\" system.

Detects when the user corrects the agent, logs failures with fault type
classification, and checks for recurring failures to trigger proactive behavior.
"""

from __future__ import annotations

import re
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.failures import check_recurring_failure, log_failure
from windyfly.memory.write_queue import Priority, WriteQueue

# Pattern → fault_type classification
FRICTION_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)(no|wrong|incorrect|that'?s not|actually)", "factual_error"),
    (r"(?i)(what i meant was|let me clarify)", "ambiguity_mishandled"),
    (r"(?i)(i (said|told you|meant))", "preference_miss"),
    (r"(?i)(try again|redo|retry|one more time)", "execution_failure"),
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
        return (
            "⚠️ RECURRING ISSUE: The user has corrected you on a similar issue recently. "
            "Be extra careful and precise. Double-check your response before sending."
        )
    else:
        return (
            "The user just corrected you. Acknowledge the correction gracefully, "
            "review what went wrong, and provide the right answer."
        )
