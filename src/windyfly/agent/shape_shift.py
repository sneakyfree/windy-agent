"""Shape-shift engine — the agent temporarily reconfigures itself for a task.

Instead of spawning an isolated sub-agent (2x token cost, no memory),
shape-shifting swaps the personality sliders, does the work with full
context and memory, then restores the original personality.

Cost advantage:  shape-shift reuses the existing conversation context
                 instead of duplicating it in a fresh LLM call.

Autonomy gating:
  - autonomy 0–3:  asks permission before shifting
  - autonomy 4–6:  announces the shift
  - autonomy 7–10: shifts silently
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Generator

from windyfly.control_panel import (
    PRESETS,
    apply_preset,
    get_sliders,
    set_slider,
)
from windyfly.memory.database import Database
from windyfly.observability.events import log_event
from windyfly.memory.write_queue import WriteQueue

logger = logging.getLogger(__name__)

# Module-level cache for saved sliders (supports nested shape-shifts)
_saved_sliders: list[dict[str, int]] = []


@contextmanager
def shape_shift(
    db: Database,
    write_queue: WriteQueue,
    target: str | dict[str, int],
    user_id: str = "default",
) -> Generator[dict[str, int], None, None]:
    """Context manager: shift personality, yield, then restore.

    Args:
        db: Database instance.
        write_queue: For event logging.
        target: Preset name (e.g. "coder") or dict of slider overrides.
        user_id: User ID.

    Yields:
        The shifted slider values.

    Example::

        with shape_shift(db, wq, "coder") as shifted:
            # agent is now in coder mode
            result = agent_respond(...)
        # agent is restored to original personality
    """
    # 1. Save current sliders to stack (supports nested shifts)
    saved = get_sliders(db, user_id)
    _saved_sliders.append(saved)

    # 2. Apply the target
    if isinstance(target, str):
        shifted = apply_preset(db, target, user_id)
        label = target
    else:
        for k, v in target.items():
            set_slider(db, k, v, user_id)
        shifted = get_sliders(db, user_id)
        label = "custom"

    log_event(db, write_queue, "shape_shift.enter", {
        "from_preset": "saved",
        "to": label,
    })
    logger.info("Shape-shifted → %s", label)

    try:
        yield shifted
    finally:
        # 3. Pop and restore original sliders
        if _saved_sliders:
            restore = _saved_sliders.pop()
        else:
            restore = saved  # Fallback
        for k, v in restore.items():
            set_slider(db, k, v, user_id)
        log_event(db, write_queue, "shape_shift.exit", {"restored_to": "saved"})
        logger.info("Shape-shift restored → original")


def get_shift_announcement(
    autonomy: int,
    target: str,
) -> str | None:
    """Get the announcement text based on autonomy level.

    Args:
        autonomy: Current autonomy slider value (0–10).
        target: Target preset or description.

    Returns:
        Announcement string, or None if silent (high autonomy).
    """
    if autonomy <= 3:
        return (
            f"I can handle this two ways:\n\n"
            f"**Option A — Shape-shift** to **{target}** mode: I reconfigure myself, "
            f"keep all our context and memories, costs half the tokens. "
            f"But I'll be in {target} mode while working — can't chat as my usual self.\n\n"
            f"**Option B — Sub-agent**: I stay here with you and spawn an isolated specialist. "
            f"Costs 2x tokens and the specialist won't know our history, "
            f"but you can keep talking to me while it works.\n\n"
            f"Which do you prefer?"
        )
    elif autonomy <= 6:
        return (
            f"Switching to **{target}** mode for this task — "
            f"I'll be more focused and efficient. Switching back when done."
        )
    else:
        return None  # Silent shift


def register_shape_shift_tool(
    registry: "ToolRegistry",
    config: dict[str, Any],
    db: Database,
    write_queue: WriteQueue,
) -> None:
    """Register shape_shift as an LLM-callable tool.

    Args:
        registry: ToolRegistry instance.
        config: Config dict.
        db: Database instance.
        write_queue: WriteQueue instance.
    """

    def _shape_shift_tool(preset: str, reason: str = "") -> str:
        """Shape-shift into a specialist mode, keeping full memory and context."""
        if preset not in PRESETS:
            return f"Unknown preset '{preset}'. Available: {list(PRESETS.keys())}"

        sliders = get_sliders(db)
        autonomy = sliders.get("autonomy", 5)
        bias = sliders.get("shape_shift_bias", 7)

        # If bias is very low, suggest sub-agent instead
        if bias <= 3:
            return (
                f"My shape-shift bias is set low ({bias}/10), which means "
                f"the user prefers isolated sub-agents. Use delegate_to_specialist "
                f"instead, or ask the user if they want me to shape-shift."
            )

        announcement = get_shift_announcement(autonomy, preset)

        # At low autonomy, ask permission
        if autonomy <= 3:
            return announcement  # type: ignore[return-value]

        # Execute the shift
        saved = get_sliders(db)
        apply_preset(db, preset)

        log_event(db, write_queue, "shape_shift.tool", {
            "preset": preset,
            "reason": reason,
            "autonomy": autonomy,
            "bias": bias,
        })

        # Build response
        prefix = f"{announcement}\n\n" if announcement else ""
        return (
            f"{prefix}"
            f"Shape-shifted to **{preset}** mode. "
            f"I still have all my memories and context — just reconfigured for this task. "
            f"Call `shape_shift_restore` when done."
        )

    def _shape_shift_restore_tool() -> str:
        """Restore personality to the state before shape-shifting."""
        if _saved_sliders:
            restore = _saved_sliders.pop()
            for k, v in restore.items():
                set_slider(db, k, v)
        log_event(db, write_queue, "shape_shift.restore", {})
        return "Personality restored to previous configuration."

    registry.register(
        name="shape_shift",
        description=(
            "Shape-shift into a specialist personality mode (e.g. 'coder', 'researcher', 'friend'). "
            "Unlike delegate_to_specialist, shape-shifting keeps ALL memory and conversation context "
            "while reconfiguring personality for the task. Uses HALF the tokens of spawning a sub-agent. "
            "Check the shape_shift_bias slider first: if it's high (7-10), prefer shape-shifting. "
            "If it's low (0-3), the user prefers sub-agents — use delegate_to_specialist instead."
        ),
        parameters={
            "type": "object",
            "properties": {
                "preset": {
                    "type": "string",
                    "description": f"Target preset: {list(PRESETS.keys())}",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief reason for the shift (shown to user at low autonomy)",
                },
            },
            "required": ["preset"],
        },
        fn=_shape_shift_tool,
    )

    registry.register(
        name="shape_shift_restore",
        description=(
            "Restore personality to the state before shape-shifting. "
            "Call this after completing the task that required the shape-shift."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        fn=_shape_shift_restore_tool,
    )

