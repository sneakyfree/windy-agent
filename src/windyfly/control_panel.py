"""Control Panel — \"The Cockpit\".

Manages personality presets, individual sliders, and cost estimation.
Three presets: buddy, engineer, powerhouse. Seven sliders: personality,
reasoning_depth, memory_depth, proactivity, autonomy, verbosity,
epistemic_strictness.
"""

from __future__ import annotations

from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.soul import get_all_soul, get_soul, upsert_soul

# Pre-defined presets
PRESETS: dict[str, dict[str, int]] = {
    "buddy": {
        "personality": 8,
        "reasoning_depth": 5,
        "memory_depth": 6,
        "proactivity": 7,
        "autonomy": 4,
        "verbosity": 6,
        "epistemic_strictness": 4,
    },
    "engineer": {
        "personality": 3,
        "reasoning_depth": 8,
        "memory_depth": 7,
        "proactivity": 3,
        "autonomy": 5,
        "verbosity": 4,
        "epistemic_strictness": 7,
    },
    "powerhouse": {
        "personality": 9,
        "reasoning_depth": 9,
        "memory_depth": 9,
        "proactivity": 8,
        "autonomy": 7,
        "verbosity": 7,
        "epistemic_strictness": 6,
    },
}

# Cost model (approximate USD per month per slider point)
_COST_PER_POINT: dict[str, float] = {
    "personality": 0.30,
    "reasoning_depth": 0.50,
    "memory_depth": 0.80,
    "proactivity": 0.50,
    "autonomy": 0.0,  # Risk, not cost
    "verbosity": 0.20,
    "epistemic_strictness": 0.0,  # Filtering, not cost
}

VALID_SLIDERS = set(_COST_PER_POINT.keys())


def apply_preset(
    db: Database,
    preset_name: str,
    user_id: str = "default",
) -> dict[str, int]:
    """Apply a preset to all sliders.

    Args:
        db: Database instance.
        preset_name: One of 'buddy', 'engineer', 'powerhouse'.
        user_id: User ID.

    Returns:
        The applied slider values.

    Raises:
        ValueError: If preset_name is not valid.
    """
    if preset_name not in PRESETS:
        raise ValueError(f"Unknown preset '{preset_name}'. Must be one of: {list(PRESETS.keys())}")

    values = PRESETS[preset_name]
    for slider_name, value in values.items():
        upsert_soul(db, key=f"slider_{slider_name}", value=str(value), source="control_panel", user_id=user_id)

    return values


def set_slider(
    db: Database,
    slider_name: str,
    value: int,
    user_id: str = "default",
) -> None:
    """Set an individual slider value.

    Args:
        db: Database instance.
        slider_name: Slider name (must be a valid slider).
        value: Slider value (1–10).
        user_id: User ID.

    Raises:
        ValueError: If slider name or value is invalid.
    """
    if slider_name not in VALID_SLIDERS:
        raise ValueError(f"Unknown slider '{slider_name}'. Must be one of: {VALID_SLIDERS}")
    if not (1 <= value <= 10):
        raise ValueError(f"Slider value must be 1–10, got {value}")

    upsert_soul(db, key=f"slider_{slider_name}", value=str(value), source="control_panel", user_id=user_id)


def get_sliders(
    db: Database,
    user_id: str = "default",
    config_defaults: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Get all current slider values.

    Falls back to config file defaults for missing sliders.

    Args:
        db: Database instance.
        user_id: User ID.
        config_defaults: Optional config personality section for fallback values.

    Returns:
        Dict of slider name → value.
    """
    defaults = config_defaults or {}
    sliders: dict[str, int] = {}

    for slider_name in VALID_SLIDERS:
        row = get_soul(db, f"slider_{slider_name}", user_id=user_id)
        if row:
            sliders[slider_name] = int(row["value"])
        else:
            sliders[slider_name] = defaults.get(slider_name, 5)

    return sliders


def estimate_monthly_cost(sliders: dict[str, int]) -> dict[str, Any]:
    """Estimate monthly cost based on slider values.

    Args:
        sliders: Dict of slider name → value.

    Returns:
        Dict with estimated_usd and per-slider breakdown.
    """
    breakdown: dict[str, float] = {}
    total = 0.0

    for name, value in sliders.items():
        cost_per = _COST_PER_POINT.get(name, 0.0)
        slider_cost = value * cost_per
        breakdown[name] = round(slider_cost, 2)
        total += slider_cost

    return {
        "estimated_usd": round(total, 2),
        "breakdown": breakdown,
    }
