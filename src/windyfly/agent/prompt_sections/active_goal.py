"""ACTIVE GOAL section (PR #204).

Conditional: emitted when `get_active_goal(db, session_id)` returns
a row. Has interpolation (the goal text is the user's typed goal),
so exposes a `render(goal_text)` function rather than a constant.
"""

from __future__ import annotations


def render_active_goal(goal_text: str) -> str:
    """Render the ACTIVE GOAL block for a given user-set goal text."""
    return (
        "🎯 ACTIVE GOAL — the user set this objective for the "
        "session. Orient every turn around concrete progress on "
        f"it.\n\n  > {goal_text}\n\n"
        "Rules while a goal is active:\n"
        "1. Don't recap the goal back at the user. They set it; "
        "they know. Just work on it.\n"
        "2. If the user goes off-topic, briefly say 'we're paused "
        "on the goal' and follow them — don't refuse.\n"
        "3. When the goal is genuinely met (deliverable produced "
        "OR user explicitly thanks you), say so explicitly — the "
        "evaluator will see your confirmation and close the goal.\n"
        "4. The user can type /goal status, /goal done, or "
        "/goal clear any time."
    )
