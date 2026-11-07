"""Personality engine — SOUL.md parser and personality prompt builder.

Loads the soul definition, applies slider modifiers, and generates
the system prompt personality block.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


# Every soul file this process has loaded, resolved. The file tools refuse these below the OWNER band whatever the
# file is called, so a soul_path pointing at a file with another name is as closed as SOUL.md (Hub's #744 note).
_LOADED_SOUL_PATHS: set[Path] = set()
# (st_dev, st_ino) of every loaded soul: a HARD link to it under another name resolves to a different path but is the
# same file, so the path set alone missed it (follow-up to #748).
_LOADED_SOUL_IDS: set[tuple[int, int]] = set()


def _file_id(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def is_loaded_soul(path: Path) -> bool:
    """True when ``path`` (already resolved) is a soul file this process has loaded, by path or as the same file."""
    if path in _LOADED_SOUL_PATHS:
        return True
    fid = _file_id(path) if _LOADED_SOUL_IDS else None
    return fid is not None and fid in _LOADED_SOUL_IDS


def load_soul(path: str = "SOUL.md") -> str:
    """Load the SOUL.md personality definition.

    Args:
        path: Path to the SOUL.md file.

    Returns:
        The raw SOUL.md contents, or a minimal default if file not found.
    """
    soul_path = Path(path)
    if soul_path.exists():
        _LOADED_SOUL_PATHS.add(soul_path.resolve())
        fid = _file_id(soul_path)
        if fid is not None:
            _LOADED_SOUL_IDS.add(fid)
        return soul_path.read_text(encoding="utf-8")

    return (
        "You are a Windy Fly agent, a personal AI companion. "
        "When you introduce yourself, use your own name if you have one. "
        "Be warm, helpful, and honest. Remember what the user tells you."
    )


# A soul section whose heading carries this marker is for the OWNER's turns only: a stranger's turn never gets it.
# Example heading:  "## Operator notes <!-- owner-only -->". The marker itself never reaches any model.
_OWNER_ONLY_MARK = re.compile(r"\s*<!--\s*owner-only\s*-->\s*", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s")
_FENCE = re.compile(r"^\s*(```|~~~)")


def soul_for_turn(soul_text: str, *, owner: bool) -> str:
    """The soul as this turn may see it.

    An owner's turn gets every section. Any other turn (a stranger, a paired non-owner) gets the soul WITHOUT the
    sections marked ``<!-- owner-only -->``: the marked heading and everything under it, up to the next heading of the
    same or a higher level (deeper sub-headings go with it). Lines inside code fences are never headings. The marker is
    removed in both cases. A soul without markers comes back unchanged, byte for byte.
    """
    if "owner-only" not in soul_text.lower():
        return soul_text
    out: list[str] = []
    fence = ""              # the open fence's marker (``` or ~~~); only the SAME kind closes it
    skip_level = 0          # > 0 while inside an owner-only section of that heading level
    for line in soul_text.splitlines(keepends=True):
        f = _FENCE.match(line)
        if f:
            if not fence:
                fence = f.group(1)
            elif f.group(1) == fence:
                fence = ""
        heading = None if fence or f else _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            if skip_level and level <= skip_level:
                skip_level = 0          # a sibling or higher heading ends the owner-only section
            if not skip_level and _OWNER_ONLY_MARK.search(line):
                if not owner:
                    skip_level = level
                    continue
        if skip_level:
            continue
        out.append(line)
    # Fail closed: a marker this parser did not take as a heading (an indented or setext heading, a marker on a body
    # line, one inside a fence) never reaches a model. A non-owner turn loses that whole LINE (and we say so once in
    # the log, so the soul can be fixed); the owner's turn keeps the line without the marker.
    kept: list[str] = []
    stray = 0
    for line in out:
        if _OWNER_ONLY_MARK.search(line):
            if not owner:
                stray += 1
                continue
            ending = "\n" if line.endswith("\n") else ""
            line = _OWNER_ONLY_MARK.sub(" ", line.rstrip("\n")).rstrip() + ending
        kept.append(line)
    if stray:
        logger.warning("soul: %d owner-only marker line(s) not on a recognised heading; dropped for a non-owner", stray)
    return "".join(kept)


# The sliders raw mode (the default) leaves out: only the tone of the personality block. Every other slider in a
# preset (autonomy, creativity, response length, reasoning depth, memory, strictness, tool rounds) still applies.
RAW_MODE_TONE_SLIDERS: tuple[str, ...] = ("personality", "humor", "formality", "verbosity")


def build_personality_block(
    soul_text: str, sliders: dict, *, raw: bool = False,
) -> str:
    """Build the personality system prompt block from SOUL.md and slider values.

    Args:
        soul_text: Raw SOUL.md text.
        sliders: Dict of slider values from config/control panel.
            Keys: personality, humor, formality, proactivity, verbosity,
                  reasoning_depth, autonomy, epistemic_strictness.
        raw: Raw-model mode (2026-07-18). When True, return the soul
            UNTOUCHED — no slider line-filtering, no tone modifiers. The
            agent's identity + memory still drive it; its tone is the
            frontier model's own native read of the room. The honest
            "don't muck it up" mode. (Autonomy's act-first/ask-first
            posture is a safety/behavior directive, not tone tuning, so
            it is still honored even in raw mode — see below.)

    Returns:
        Final personality prompt block (targeted under 600 tokens).
    """
    if raw:
        # Native model + soul + memory. The one thing we still append is
        # the autonomy posture, because "act first vs ask first" is a
        # behavior/safety contract the owner set, not a tone knob.
        result = soul_text.strip()
        autonomy = sliders.get("autonomy", 5)
        if autonomy >= 7:
            result += (
                "\n\nACT FIRST — use your tools immediately, state your "
                "assumption in one line, ask only as a last resort."
            )
        elif autonomy <= 3:
            result += (
                "\n\nAsk before acting — confirm intent before invoking "
                "any state-changing tool."
            )
        return result
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
        adapted["verbosity"] = min(adapted.get("verbosity", 5), 3)
    elif emotional_context == "stressed":
        # Single-message stress: soften but don't overreact
        adapted["humor"] = min(adapted.get("humor", 5), 1)
        adapted["verbosity"] = min(adapted.get("verbosity", 5), 4)
    elif emotional_context == "excited" or emotional_trend == "excited":
        # Match their energy
        adapted["humor"] = min(adapted.get("humor", 5) + 2, 10)

    return adapted
