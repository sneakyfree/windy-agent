"""Friction and failure detection — the \"Never Wrong Twice\" system.

Detects when the user corrects the agent, logs failures with fault type
classification, and checks for recurring failures to trigger proactive behavior.
"""

from __future__ import annotations

import re
from typing import Any

import logging

from windyfly.memory.database import Database
from windyfly.memory.failures import check_recurring_failure, get_recent_failures, log_failure
from windyfly.memory.skills import save_skill
from windyfly.memory.write_queue import Priority, WriteQueue

logger = logging.getLogger(__name__)

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

    # Check for recurring failure before logging
    is_recurring = check_recurring_failure(db, fault_type, description)

    # Create a correction skill for recurring failures
    correction_skill_id = None
    if is_recurring:
        try:
            correction_code = _build_correction_code(fault_type, friction)
            correction_skill_id = save_skill(
                db,
                name=f"correction-{fault_type}",
                code=correction_code,
                language="python",
                description=f"Auto-generated correction for recurring {fault_type}",
                risk_level="low",
            )
            # Auto-promote so the skill is actually USED in future
            # turns. Without auto-promotion, correction skills sit
            # in the DB unread — the "evolves over time" claim was
            # silently broken from launch through 2026-05-20 v18
            # finding. Low risk_level + low-confidence advice
            # ("double-check facts") justifies opt-out-not-opt-in
            # promotion. Operators can demote manually via the
            # bridge UDS server.
            try:
                from windyfly.skills.manager import promote_skill
                promote_skill(db, correction_skill_id)
            except Exception as pe:
                logger.debug(
                    "Could not auto-promote correction skill: %s", pe,
                )
        except Exception as e:
            logger.debug("Could not create correction skill: %s", e)

    # Log the failure with linked skill (HIGH priority)
    write_queue.enqueue(
        Priority.HIGH,
        log_failure,
        db,
        fault_type,
        description,
        correction_skill_id=correction_skill_id,
    )

    # Log event for observability (G12)
    from windyfly.observability.events import log_event
    log_event(db, write_queue, "failure.detect", {
        "fault_type": fault_type,
        "pattern": friction.get("pattern_matched", ""),
        "correction_skill_id": correction_skill_id,
    })

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


def _build_correction_code(fault_type: str, friction: dict[str, Any]) -> str:
    """Generate correction skill code from a friction event.

    Creates a simple rule-based skill that can be applied to future
    prompts to avoid the same class of mistakes.
    """
    user_msg = friction.get("user_message", "")[:150]
    agent_msg = friction.get("agent_message", "")[:150]

    return (
        f"# Auto-generated correction skill for: {fault_type}\n"
        f"# Triggered by user feedback: {user_msg!r}\n"
        f"# Agent's incorrect response: {agent_msg!r}\n"
        f"\n"
        f"FAULT_TYPE = {fault_type!r}\n"
        f"CORRECTION = (\n"
        f"    'When handling {fault_type} situations, '\n"
        f"    'double-check facts and acknowledge user corrections promptly.'\n"
        f")\n"
    )
