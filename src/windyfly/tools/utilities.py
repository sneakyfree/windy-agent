"""Everyday utility tools — timer, translate, convert, random.

Small tools that make the agent feel like a real assistant.
No API keys required for any of these.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from windyfly.tools.registry import ToolRegistry


# ── Timer / Countdown ───────────────────────────────────────────

# Per-process active-timer registry. Pre-fix had two bugs:
#   1. ID generated via len()+1 — collided after expiry/cleanup
#   2. Expired timers were never removed, growing the dict forever
#      in long-running processes.
# Now: UUID-based IDs eliminate collision; _purge_expired() runs
# on every set/list call so the dict can't grow without bound.
_active_timers: dict[str, datetime] = {}


def _purge_expired() -> None:
    """Drop timers whose end_time is in the past. O(N) per call,
    but N is the active-timer count (typically tiny), so cheap."""
    now = datetime.now(timezone.utc)
    expired = [k for k, end in _active_timers.items() if end <= now]
    for k in expired:
        _active_timers.pop(k, None)


def set_timer(duration: str, label: str = "Timer") -> dict[str, Any]:
    """Set a countdown timer.

    Args:
        duration: "20 minutes", "5 min", "1 hour", "30 seconds", "90s"
        label: Optional label for the timer.
    """
    seconds = _parse_duration(duration)
    if seconds is None:
        return {"success": False, "error": f"Could not parse duration: {duration}"}

    _purge_expired()
    end_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    # UUID-based ID can't collide with an expired-but-not-yet-purged
    # entry the way "timer-N" (counter from len()) could.
    import uuid
    timer_id = f"timer-{uuid.uuid4().hex[:8]}"
    _active_timers[timer_id] = end_time

    if seconds < 60:
        human = f"{seconds} seconds"
    elif seconds < 3600:
        human = f"{seconds // 60} minute{'s' if seconds >= 120 else ''}"
    else:
        human = f"{seconds // 3600} hour{'s' if seconds >= 7200 else ''}"

    return {
        "success": True,
        "id": timer_id,
        "message": f"⏱️ {label}: {human} timer started. I'll let you know when it's done!",
        "ends_at": end_time.isoformat(),
        "seconds": seconds,
    }


def _parse_duration(s: str) -> int | None:
    """Parse a duration string to seconds."""
    s = s.strip().lower()
    m = re.match(r"(\d+)\s*(s(?:ec(?:ond)?s?)?|m(?:in(?:ute)?s?)?|h(?:(?:ou)?rs?)?)", s)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)[0]
        if unit == "s":
            return amount
        if unit == "m":
            return amount * 60
        if unit == "h":
            return amount * 3600
    # Try plain number as minutes
    if s.isdigit():
        return int(s) * 60
    return None


# ── Unit Conversion ─────────────────────────────────────────────

# None marks unit pairs that need an affine formula (C↔F) rather than a
# simple multiplier; callers dispatch on `is None` and fall through to a
# special-case code path.
_CONVERSIONS: dict[tuple[str, str], float | None] = {
    ("km", "miles"): 0.621371, ("miles", "km"): 1.60934,
    ("kg", "lbs"): 2.20462, ("lbs", "kg"): 0.453592,
    ("c", "f"): None, ("f", "c"): None,  # Special handling
    ("cm", "inches"): 0.393701, ("inches", "cm"): 2.54,
    ("m", "feet"): 3.28084, ("feet", "m"): 0.3048,
    ("liters", "gallons"): 0.264172, ("gallons", "liters"): 3.78541,
    ("oz", "g"): 28.3495, ("g", "oz"): 0.035274,
}

# Aliases
_UNIT_ALIASES: dict[str, str] = {
    "kilometer": "km", "kilometers": "km", "kilometre": "km",
    "mile": "miles", "mi": "miles",
    "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "kilos": "kg",
    "pound": "lbs", "pounds": "lbs", "lb": "lbs",
    "celsius": "c", "fahrenheit": "f",
    "centimeter": "cm", "centimeters": "cm", "centimetre": "cm",
    "inch": "inches", "in": "inches",
    "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "foot": "feet", "ft": "feet",
    "liter": "liters", "litres": "liters", "l": "liters",
    "gallon": "gallons", "gal": "gallons",
    "ounce": "oz", "ounces": "oz",
    "gram": "g", "grams": "g",
}


def convert_units(value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
    """Convert between common units."""
    fr = _UNIT_ALIASES.get(from_unit.lower(), from_unit.lower())
    to = _UNIT_ALIASES.get(to_unit.lower(), to_unit.lower())

    # Temperature special cases
    if fr == "c" and to == "f":
        result = value * 9 / 5 + 32
        return {"result": round(result, 1), "message": f"{value}°C = {result:.1f}°F"}
    if fr == "f" and to == "c":
        result = (value - 32) * 5 / 9
        return {"result": round(result, 1), "message": f"{value}°F = {result:.1f}°C"}

    factor = _CONVERSIONS.get((fr, to))
    if factor is None:
        return {"error": f"Don't know how to convert {fr} to {to}"}

    result = value * factor
    return {"result": round(result, 4), "message": f"{value} {fr} = {result:.2f} {to}"}


# ── Random / Dice / Coin ────────────────────────────────────────

def flip_coin() -> dict[str, str]:
    """Flip a coin."""
    result = random.choice(["Heads", "Tails"])
    return {"result": result, "message": f"🪙 {result}!"}


def roll_dice(sides: int = 6, count: int = 1) -> dict[str, Any]:
    """Roll dice."""
    if sides < 2 or sides > 100:
        return {"error": "Sides must be between 2 and 100"}
    if count < 1 or count > 20:
        return {"error": "Count must be between 1 and 20"}

    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls)

    if count == 1:
        msg = f"🎲 Rolled a {rolls[0]} (d{sides})"
    else:
        msg = f"🎲 Rolled {count}d{sides}: {rolls} = {total}"

    return {"rolls": rolls, "total": total, "message": msg}


def random_number(min_val: int = 1, max_val: int = 100) -> dict[str, Any]:
    """Generate a random number in range."""
    if min_val > max_val:
        min_val, max_val = max_val, min_val
    n = random.randint(min_val, max_val)
    return {"result": n, "message": f"🎯 Random number ({min_val}–{max_val}): {n}"}


# ── Calculate ────────────────────────────────────────────────────

def calculate(expression: str) -> dict[str, Any]:
    """Evaluate a math expression safely.

    Supports: +, -, *, /, **, %, parentheses, common math functions.
    """
    import math

    # Whitelist safe operations
    allowed = set("0123456789.+-*/%()")
    cleaned = expression.replace(" ", "").replace("^", "**")

    # Allow math function names
    safe_names = {"sqrt", "sin", "cos", "tan", "log", "log10", "pi", "e", "abs", "round", "pow"}
    test = cleaned
    for name in safe_names:
        test = test.replace(name, "")

    if not all(c in allowed or c.isalpha() for c in test):
        return {"error": "Expression contains invalid characters"}

    try:
        # Safe eval with math namespace only
        result = eval(cleaned, {"__builtins__": {}}, {
            "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
            "tan": math.tan, "log": math.log, "log10": math.log10,
            "pi": math.pi, "e": math.e, "abs": abs, "round": round, "pow": pow,
        })
        return {"result": result, "message": f"🧮 {expression} = {result}"}
    except Exception as e:
        return {"error": f"Could not calculate: {e}"}


# ── Registration ─────────────────────────────────────────────────

def register_utility_tools(registry: ToolRegistry) -> None:
    """Register all utility tools with the LLM."""
    registry.register(
        name="set_timer",
        description="Set a countdown timer. Use for 'Set a timer for 20 minutes', 'Timer 5 min'.",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "string", "description": "Duration: '20 minutes', '5 min', '1 hour', '30s'"},
                "label": {"type": "string", "description": "Optional label for the timer"},
            },
            "required": ["duration"],
        },
        fn=set_timer,
    )

    registry.register(
        name="convert_units",
        description=(
            "Convert between units. Use for 'How many miles is 10 km?', "
            "'Convert 72°F to Celsius', '5 pounds in kg'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "value": {"type": "number", "description": "The value to convert"},
                "from_unit": {"type": "string", "description": "Source unit (km, miles, kg, lbs, c, f, etc.)"},
                "to_unit": {"type": "string", "description": "Target unit"},
            },
            "required": ["value", "from_unit", "to_unit"],
        },
        fn=convert_units,
    )

    registry.register(
        name="flip_coin",
        description="Flip a coin. Returns Heads or Tails.",
        parameters={"type": "object", "properties": {}},
        fn=flip_coin,
    )

    registry.register(
        name="roll_dice",
        description="Roll dice. Use for 'Roll a d20', 'Roll 2d6', 'Roll the dice'.",
        parameters={
            "type": "object",
            "properties": {
                "sides": {"type": "integer", "description": "Number of sides (default: 6)"},
                "count": {"type": "integer", "description": "Number of dice (default: 1)"},
            },
        },
        fn=roll_dice,
    )

    registry.register(
        name="calculate",
        description=(
            "Calculate a math expression. Use for 'What's 15% of 230?', "
            "'Calculate 3.14 * 5^2', 'sqrt(144)'. Supports +, -, *, /, **, sqrt, sin, cos, log."
        ),
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression to evaluate"},
            },
            "required": ["expression"],
        },
        fn=calculate,
    )
