"""Mode switching for Windy Fly personality.

Manages the current agent mode: companion, focused, or neutral.
"""

from __future__ import annotations

VALID_MODES = {"companion", "focused", "neutral"}
DEFAULT_MODE = "companion"


def validate_mode(mode: str) -> str:
    """Validate and return a mode string.

    Args:
        mode: Requested mode.

    Returns:
        Validated mode string.

    Raises:
        ValueError: If mode is not valid.
    """
    mode = mode.lower().strip()
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode '{mode}'. Must be one of: {VALID_MODES}")
    return mode
