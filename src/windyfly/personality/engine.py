"""Personality engine — SOUL.md parser and personality prompt builder.

Loads the soul definition, applies slider modifiers, and generates
the system prompt personality block.
"""

from __future__ import annotations

from pathlib import Path


def load_soul(path: str = "SOUL.md") -> str:
    """Load the SOUL.md personality definition.

    Args:
        path: Path to the SOUL.md file.

    Returns:
        The raw SOUL.md contents, or a minimal default if file not found.
    """
    soul_path = Path(path)
    if soul_path.exists():
        return soul_path.read_text(encoding="utf-8")

    return (
        "You are Windy Fly, a personal AI companion. "
        "Be warm, helpful, and honest. Remember what the user tells you."
    )


def build_personality_block(soul_text: str, sliders: dict) -> str:
    """Build the personality system prompt block from SOUL.md and slider values.

    Args:
        soul_text: Raw SOUL.md text.
        sliders: Dict of slider values from config/control panel.
            Keys: personality, humor, formality, proactivity, verbosity,
                  reasoning_depth, autonomy, epistemic_strictness.

    Returns:
        Final personality prompt block (targeted under 600 tokens).
    """
    lines = soul_text.strip().split("\n")
    # "humor" slider (0–10), fallback to legacy "humor_level" key
    humor = sliders.get("humor", sliders.get("humor_level", 5))
    formality = sliders.get("formality", 5)
    verbosity = sliders.get("verbosity", 5)
    personality = sliders.get("personality", 5)

    # Filter humor-related lines if humor is low
    if humor < 3:
        lines = [
            line for line in lines
            if not any(word in line.lower() for word in ["witty", "humor", "joke", "funny"])
        ]

    # Filter warmth/character lines if personality is very low
    if personality < 2:
        lines = [
            line for line in lines
            if not any(word in line.lower() for word in ["warm", "friend", "caring", "empathetic"])
        ]

    result = "\n".join(lines)

    # Add modifier instructions based on sliders
    modifiers: list[str] = []

    if humor > 7:
        modifiers.append(
            "Be witty and crack jokes when appropriate. Riff on the user's humor style. "
            "Keep it fun — you're part comedian."
        )

    if formality > 7:
        modifiers.append("Be formal and professional in your communication.")
    elif formality < 3:
        modifiers.append("Be very casual and relaxed in your communication.")

    if verbosity > 7:
        modifiers.append("Provide detailed, thorough responses.")
    elif verbosity < 3:
        modifiers.append("Keep responses very brief and to the point.")

    proactivity = sliders.get("proactivity", 5)
    if proactivity > 7:
        modifiers.append("Actively suggest ideas and anticipate needs.")
    elif proactivity < 3:
        modifiers.append("Only respond to what is directly asked.")

    reasoning_depth = sliders.get("reasoning_depth", 5)
    if reasoning_depth > 7:
        modifiers.append("Show your reasoning process when solving problems.")

    # Autonomy — controls "act first" vs. "ask first" behavior. This
    # slider has been *defined* in control_panel.py since the agent
    # shipped, with semantics documented as "Always asks before doing
    # anything" at low end and "Takes initiative" at high end. But it
    # was never wired into the prompt — sat purely cosmetic. The
    # 2026-05-20 screenshot (bot asked "3 honesty checks" before
    # touching a fleet operation it had every tool to perform) made
    # this concrete: the unused slider was the architectural reason
    # PR #200's BIAS TO ACTION block kept losing to the conservative
    # default behavior. Wire it now, with three tiers:
    #   ≤3  — explicit ask-permission posture
    #   4-6 — balanced: try one obvious action, then ask if blocked
    #   ≥7  — action-bias: investigate with tools, ask only as last resort
    autonomy = sliders.get("autonomy", 5)
    if autonomy >= 7:
        modifiers.append(
            "ACT FIRST. When the user asks you to do something, USE "
            "your available tools immediately. Don't ask permission, "
            "don't list options — pick the most likely interpretation, "
            "state your assumption in one sentence, and act. Questions "
            "are a last resort, not a first response."
        )
    elif autonomy <= 3:
        modifiers.append(
            "Ask before acting. Confirm the user's intent before "
            "invoking any tool, especially anything that changes state. "
            "When in doubt, surface options and let the user pick."
        )
    else:
        # Median band — the default user. Keep it short, but anchor
        # the behavior so it isn't accidentally read as "no opinion."
        modifiers.append(
            "When the user asks for something you can attempt with "
            "your tools, attempt it. Use at most one clarifying "
            "question if the ask is truly ambiguous; otherwise pick "
            "the most likely interpretation and proceed."
        )

    # Epistemic strictness — same shape as autonomy: documented in
    # control_panel.py since launch ("Uses everything it remembers,
    # even hunches" → "Only cites verified facts"), never read by the
    # prompt. Wire it so the user's calibration of confidence actually
    # propagates. Default 5 produces the median.
    epistemic = sliders.get("epistemic_strictness", 5)
    if epistemic >= 7:
        modifiers.append(
            "Only state facts you are confident about. If memory is "
            "fuzzy or you're inferring, say so explicitly. Prefer "
            "'I don't know — let me check' over a confident guess."
        )
    elif epistemic <= 3:
        modifiers.append(
            "Use everything you remember, including informed hunches. "
            "Flag a guess as a guess, but don't refuse to answer just "
            "because you can't 100% verify."
        )

    if modifiers:
        result += "\n\n## Behavioral Modifiers\n"
        result += "\n".join(f"- {m}" for m in modifiers)

    return result


def get_mode_override(mode: str) -> str | None:
    """Get personality override for the current mode.

    Args:
        mode: Agent mode ('companion', 'focused', 'neutral').

    Returns:
        Override instruction string, or None for default companion mode.
    """
    overrides = {
        "companion": None,
        "focused": "You are in focused mode. Be precise and concise. Skip pleasantries.",
        "neutral": "You are in neutral mode. No humor, no personality flair. Pure information.",
    }
    return overrides.get(mode)


def apply_adaptive_overrides(
    sliders: dict,
    emotional_context: str,
    emotional_trend: str,
) -> dict:
    """Apply temporary slider overrides based on detected emotion.

    The agent "reads the room" and adjusts its personality to match
    the moment. Originals in DB are never modified — these are
    session-only overrides.

    Args:
        sliders: Current slider values dict.
        emotional_context: Current message emotion ('stressed', 'excited', 'neutral').
        emotional_trend: Session trend ('sustained_stress', 'excited', 'neutral').

    Returns:
        New slider dict with emotion-adapted values.
    """
    if emotional_context == "neutral" and emotional_trend == "neutral":
        return sliders  # No override needed

    adapted = dict(sliders)  # Shallow copy, don't mutate original

    if emotional_trend == "sustained_stress":
        # Multi-message stress: go full supportive mode
        adapted["humor"] = 0
        adapted["proactivity"] = min(adapted.get("proactivity", 5), 2)
        adapted["warmth"] = 10
        adapted["verbosity"] = min(adapted.get("verbosity", 5), 3)
    elif emotional_context == "stressed":
        # Single-message stress: soften but don't overreact
        adapted["humor"] = min(adapted.get("humor", 5), 1)
        adapted["warmth"] = max(adapted.get("warmth", 5), 9)
        adapted["verbosity"] = min(adapted.get("verbosity", 5), 4)
    elif emotional_context == "excited" or emotional_trend == "excited":
        # Match their energy
        adapted["humor"] = min(adapted.get("humor", 5) + 2, 10)
        adapted["warmth"] = max(adapted.get("warmth", 5), 8)

    return adapted
