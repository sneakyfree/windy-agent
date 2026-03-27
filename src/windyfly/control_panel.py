"""Control Panel — "The Cockpit".

Manages personality presets, individual sliders, and cost estimation.
Three tiers: 8 presets, 15 sliders (0–10), and per-slider cost model.

Sliders:
  personality, humor, formality, reasoning_depth, creativity,
  memory_depth, context_window, proactivity, autonomy, verbosity,
  response_length, epistemic_strictness, tool_reloop_rounds,
  emotional_sensitivity, memory_retention.
"""

from __future__ import annotations

from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.soul import get_all_soul, get_soul, upsert_soul

# ---------------------------------------------------------------------------
# Slider metadata — label, description, impact at low/high ends
# ---------------------------------------------------------------------------
SLIDER_INFO: dict[str, dict[str, str]] = {
    "personality": {
        "label": "Personality",
        "description": "How much warmth, character, and soul the agent puts into responses.",
        "impact_low": "Robotic, clinical responses. Zero flair. Saves ~3% of tokens.",
        "impact_high": "Full SOUL.md personality, warm, human-like. Costs ~3% more tokens on personality injection.",
    },
    "humor": {
        "label": "Humor",
        "description": "How much humor, wit, and playfulness the agent brings.",
        "impact_low": "Stick-in-the-mud. No jokes, no riffing. Pure business.",
        "impact_high": "Jim Carrey energy. Cracks jokes, riffs on your style, keeps it fun. Minimal extra token cost.",
    },
    "formality": {
        "label": "Formality",
        "description": "Tone register — from casual texting to boardroom professional.",
        "impact_low": "\"yo what's good\" — relaxed, slang-friendly, abbreviations.",
        "impact_high": "\"Dear esteemed colleague\" — proper grammar, no contractions, corporate-ready.",
    },
    "reasoning_depth": {
        "label": "Reasoning Depth",
        "description": "How much the agent shows its thinking process.",
        "impact_low": "Quick gut-reaction answers. Fast but no explanation.",
        "impact_high": "Full chain-of-thought reasoning. You see exactly how it got there. ~20% more tokens.",
    },
    "creativity": {
        "label": "Creativity",
        "description": "Controls LLM temperature — how predictable vs. imaginative responses are.",
        "impact_low": "Precise, deterministic. Same question = same answer. Best for code and facts.",
        "impact_high": "Wild, varied, surprising responses. Great for brainstorming. May hallucinate more.",
    },
    "memory_depth": {
        "label": "Memory Depth",
        "description": "How many knowledge facts about you are injected into every conversation.",
        "impact_low": "Remembers almost nothing about you. Light and fast.",
        "impact_high": "Full life-graph recall — your name, preferences, history. Costs ~5-10% of your token budget.",
    },
    "context_window": {
        "label": "Context Window",
        "description": "How many past messages the agent carries in the conversation.",
        "impact_low": "5 recent messages. Short memory within a chat. Very cheap.",
        "impact_high": "50 recent messages. Full conversation recall. Burns ~15% of token budget on history.",
    },
    "proactivity": {
        "label": "Proactivity",
        "description": "Whether the agent volunteers ideas or only answers what's asked.",
        "impact_low": "Only answers your exact question. Never suggests, never nudges.",
        "impact_high": "Actively suggests ideas, flags things you might have missed, anticipates your needs.",
    },
    "autonomy": {
        "label": "Autonomy",
        "description": "How much the agent acts on its own vs. asking permission first.",
        "impact_low": "Always asks before doing anything. Maximum control.",
        "impact_high": "Takes initiative — executes tasks, makes calls, acts independently. Use with caution.",
    },
    "verbosity": {
        "label": "Verbosity",
        "description": "Response style — from terse one-liners to thorough explanations.",
        "impact_low": "Bullet points and one-liners. Maximum density.",
        "impact_high": "Rich, detailed responses with examples and context. ~30% more tokens.",
    },
    "response_length": {
        "label": "Response Length",
        "description": "Hard cap on how long each response can be (token limit).",
        "impact_low": "250 token cap (~2 paragraphs max). Fast and cheap.",
        "impact_high": "4,000 token cap (~3 pages). Full essays when needed. Directly scales cost.",
    },
    "epistemic_strictness": {
        "label": "Epistemic Strictness",
        "description": "How much the agent trusts its own memory vs. only citing verified facts.",
        "impact_low": "Uses everything it remembers, even hunches. More helpful, less precise.",
        "impact_high": "Only cites verified facts. Refuses to guess. Safer but may miss context.",
    },
    "tool_reloop_rounds": {
        "label": "Tool Use Depth",
        "description": "Max rounds of tool execution per response (web search, API calls, etc.).",
        "impact_low": "1 round — one tool call, then answers. Fast.",
        "impact_high": "10 rounds — deep research, chaining tool calls. Burns 2-10x tokens per response.",
    },
    "emotional_sensitivity": {
        "label": "Emotional Sensitivity",
        "description": "How attuned the agent is to your emotional state (stress, excitement).",
        "impact_low": "Ignores your mood entirely. Pure information.",
        "impact_high": "Detects frustration, adjusts tone, offers support. Scans last 10 messages for patterns. Minimal cost.",
    },
    "memory_retention": {
        "label": "Memory Retention",
        "description": "How long the agent holds onto old memories before they fade.",
        "impact_low": "Goldfish \U0001f420 — aggressive forgetting, old facts decay fast. Saves ~1% of token budget.",
        "impact_high": "Elephant \U0001f418 — never forgets. Old memories maintained indefinitely. Costs ~10% of token budget on retention.",
    },
    "warmth": {
        "label": "Warmth",
        "description": "How emotionally warm and supportive the agent is.",
        "impact_low": "Clinical, detached. Facts only.",
        "impact_high": "Warm, caring, empathetic. Like a close friend.",
    },
    "adaptive_mode": {
        "label": "Adaptive Mode",
        "description": "When ON, the agent reads your mood and temporarily adjusts its personality to match.",
        "impact_low": "Sliders stay exactly where you set them. Full manual control.",
        "impact_high": "Agent 'reads the room' — softens when you're stressed, matches energy when you're excited.",
    },
    "shape_shift_bias": {
        "label": "Shape-Shift Bias",
        "description": "When specialist work is needed, controls whether the agent reconfigures itself (shape-shift) or spawns an isolated sub-agent.",
        "impact_low": "Always spawns a separate sub-agent. Clean slate, zero memory bleed, but costs 2x tokens.",
        "impact_high": "Always shape-shifts in place. Keeps all memory and context, half the token cost. Your best friend becomes the specialist.",
    },
}

# ---------------------------------------------------------------------------
# Pre-defined presets (8 total)
# ---------------------------------------------------------------------------
PRESETS: dict[str, dict[str, int]] = {
    "buddy": {
        "personality": 8,
        "humor": 7,
        "formality": 4,
        "reasoning_depth": 5,
        "creativity": 6,
        "memory_depth": 6,
        "context_window": 5,
        "proactivity": 7,
        "autonomy": 4,
        "verbosity": 6,
        "response_length": 5,
        "epistemic_strictness": 4,
        "tool_reloop_rounds": 2,
        "emotional_sensitivity": 6,
        "memory_retention": 6,
        "warmth": 7,
        "adaptive_mode": 8,
        "shape_shift_bias": 8,
    },
    "engineer": {
        "personality": 3,
        "humor": 1,
        "formality": 5,
        "reasoning_depth": 8,
        "creativity": 3,
        "memory_depth": 7,
        "context_window": 7,
        "proactivity": 3,
        "autonomy": 5,
        "verbosity": 4,
        "response_length": 7,
        "epistemic_strictness": 7,
        "tool_reloop_rounds": 5,
        "emotional_sensitivity": 2,
        "memory_retention": 7,
        "warmth": 3,
        "adaptive_mode": 2,
        "shape_shift_bias": 4,
    },
    "powerhouse": {
        "personality": 9,
        "humor": 7,
        "formality": 5,
        "reasoning_depth": 9,
        "creativity": 7,
        "memory_depth": 9,
        "context_window": 9,
        "proactivity": 8,
        "autonomy": 7,
        "verbosity": 7,
        "response_length": 9,
        "epistemic_strictness": 6,
        "tool_reloop_rounds": 8,
        "emotional_sensitivity": 7,
        "memory_retention": 9,
        "warmth": 7,
        "adaptive_mode": 7,
        "shape_shift_bias": 7,
    },
    "coder": {
        "personality": 1,
        "humor": 0,
        "formality": 2,
        "reasoning_depth": 10,
        "creativity": 4,
        "memory_depth": 6,
        "context_window": 8,
        "proactivity": 3,
        "autonomy": 5,
        "verbosity": 3,
        "response_length": 10,
        "epistemic_strictness": 8,
        "tool_reloop_rounds": 6,
        "emotional_sensitivity": 0,
        "memory_retention": 8,
        "warmth": 1,
        "adaptive_mode": 0,
        "shape_shift_bias": 3,
    },
    "friend": {
        "personality": 10,
        "humor": 3,
        "formality": 2,
        "reasoning_depth": 3,
        "creativity": 5,
        "memory_depth": 8,
        "context_window": 8,
        "proactivity": 8,
        "autonomy": 3,
        "verbosity": 7,
        "response_length": 6,
        "epistemic_strictness": 3,
        "tool_reloop_rounds": 1,
        "emotional_sensitivity": 10,
        "memory_retention": 9,
        "warmth": 10,
        "adaptive_mode": 10,
        "shape_shift_bias": 10,
    },
    "writer": {
        "personality": 7,
        "humor": 5,
        "formality": 5,
        "reasoning_depth": 6,
        "creativity": 10,
        "memory_depth": 5,
        "context_window": 6,
        "proactivity": 6,
        "autonomy": 4,
        "verbosity": 9,
        "response_length": 9,
        "epistemic_strictness": 3,
        "tool_reloop_rounds": 3,
        "emotional_sensitivity": 5,
        "memory_retention": 5,
        "warmth": 6,
        "adaptive_mode": 5,
        "shape_shift_bias": 7,
    },
    "researcher": {
        "personality": 2,
        "humor": 0,
        "formality": 7,
        "reasoning_depth": 10,
        "creativity": 3,
        "memory_depth": 9,
        "context_window": 9,
        "proactivity": 5,
        "autonomy": 4,
        "verbosity": 7,
        "response_length": 8,
        "epistemic_strictness": 9,
        "tool_reloop_rounds": 8,
        "emotional_sensitivity": 1,
        "memory_retention": 9,
        "warmth": 2,
        "adaptive_mode": 1,
        "shape_shift_bias": 3,
    },
    "silent": {
        "personality": 1,
        "humor": 0,
        "formality": 5,
        "reasoning_depth": 4,
        "creativity": 3,
        "memory_depth": 3,
        "context_window": 3,
        "proactivity": 1,
        "autonomy": 2,
        "verbosity": 1,
        "response_length": 2,
        "epistemic_strictness": 5,
        "tool_reloop_rounds": 1,
        "emotional_sensitivity": 1,
        "memory_retention": 3,
        "warmth": 3,
        "adaptive_mode": 0,
        "shape_shift_bias": 5,
    },
}

# ---------------------------------------------------------------------------
# Cost model (approximate USD per month per slider point)
# ---------------------------------------------------------------------------
_COST_PER_POINT: dict[str, float] = {
    "personality": 0.30,
    "humor": 0.20,
    "formality": 0.00,       # Style, not cost
    "reasoning_depth": 0.50,
    "creativity": 0.10,
    "memory_depth": 0.80,
    "context_window": 0.60,
    "proactivity": 0.50,
    "autonomy": 0.00,        # Risk, not cost
    "verbosity": 0.20,
    "response_length": 0.70,
    "epistemic_strictness": 0.00,  # Filtering, not cost
    "tool_reloop_rounds": 0.40,
    "emotional_sensitivity": 0.10,
    "memory_retention": 0.50,
    "warmth": 0.10,
    "adaptive_mode": 0.05,
    "shape_shift_bias": 0.00,  # Strategy, not cost — shifts are free vs sub-agents
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
        preset_name: One of the preset names (buddy, engineer, etc.).
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
        value: Slider value (0–10).
        user_id: User ID.

    Raises:
        ValueError: If slider name or value is invalid.
    """
    if slider_name not in VALID_SLIDERS:
        raise ValueError(f"Unknown slider '{slider_name}'. Must be one of: {VALID_SLIDERS}")
    if not (0 <= value <= 10):
        raise ValueError(f"Slider value must be 0–10, got {value}")

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


def get_slider_info(
    db: Database,
    user_id: str = "default",
    config_defaults: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Get all slider metadata including current values, descriptions, and cost impact.

    This is what the UI/dashboard uses to render the slider panel.

    Args:
        db: Database instance.
        user_id: User ID.
        config_defaults: Optional config personality section for fallback values.

    Returns:
        Dict of slider name → {label, description, impact_low, impact_high, value, cost_per_point}.
    """
    current_values = get_sliders(db, user_id=user_id, config_defaults=config_defaults)
    result: dict[str, dict[str, Any]] = {}

    for slider_name in VALID_SLIDERS:
        info = SLIDER_INFO.get(slider_name, {})
        result[slider_name] = {
            "label": info.get("label", slider_name),
            "description": info.get("description", ""),
            "impact_low": info.get("impact_low", ""),
            "impact_high": info.get("impact_high", ""),
            "value": current_values.get(slider_name, 5),
            "cost_per_point": _COST_PER_POINT.get(slider_name, 0.0),
        }

    return result


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
