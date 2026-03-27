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
