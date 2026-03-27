"""Soul Passport preview formatter for Soul Continuity.

Generates a human-readable preview of imported data for user approval
before any data is written to the database.
"""

from __future__ import annotations

from typing import Any


def format_soul_preview(parsed_data: dict[str, Any]) -> str:
    """Format a human-readable soul preview from parsed import data.

    This is the most important UX in the app — the user sees this
    before any data is written.

    Args:
        parsed_data: Standardized import dict from a parser.

    Returns:
        Formatted preview string for user display.
    """
    source = parsed_data.get("source", "unknown")
    personality = parsed_data.get("personality", {})
    memories = parsed_data.get("memories", [])
    skills = parsed_data.get("skills", [])

    lines: list[str] = []
    lines.append(f"🧬 Soul Preview — Import from {source}")
    lines.append("")

    # Personality traits
    traits = personality.get("traits", [])
    humor = personality.get("humor")
    formality = personality.get("formality")

    lines.append(f"📝 Personality traits found: {len(traits)}")
    if humor is not None:
        lines.append(f"  - Humor level: {humor}/10")
    if formality is not None:
        lines.append(f"  - Formality: {formality}/10")
    if traits:
        for trait in traits[:5]:
            lines.append(f"  - {trait}")
        if len(traits) > 5:
            lines.append(f"  ... and {len(traits) - 5} more")
    lines.append("")

    # Classify memories
    safe = [m for m in memories if m.get("type") in ("fact", "preference", "topic", "conversation")]
    sensitive = [m for m in memories if m.get("type") in ("belief", "identity")]
    total_memories = len(memories)

    lines.append(f"🧠 Memories found: {total_memories}")
    lines.append(f"  ✅ Safe (auto-import): {len(safe)} preferences, facts, topics")
    lines.append(f"  ⚠️  Sensitive (needs review): {len(sensitive)} beliefs, identity facts")
    if skills:
        lines.append(f"  🔒 Executable (sandbox required): {len(skills)} skill(s)")
    lines.append("")

    # Confidence notice
    lines.append("📊 Confidence: All imports marked at 50% confidence")
    lines.append("  You can verify or dismiss any imported fact later.")
    lines.append("")

    # Action prompt
    lines.append("Would you like to proceed? (yes/no/review-sensitive)")

    return "\n".join(lines)


def classify_memory(memory: dict[str, Any]) -> str:
    """Classify a memory as safe, sensitive, or executable.

    Args:
        memory: Memory dict with type field.

    Returns:
        Classification: 'safe', 'sensitive', or 'executable'.
    """
    mem_type = memory.get("type", "")
    if mem_type in ("belief", "identity"):
        return "sensitive"
    return "safe"
