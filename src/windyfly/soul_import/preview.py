"""Soul Passport preview formatter for Soul Continuity.

Generates a human-readable preview of imported data for user approval
before any data is written to the database.
"""

from __future__ import annotations

import re
from typing import Any

#: Imported skill formats that are prose a person or agent reads. These are
#: stored as text playbooks; anything else (python, js, shell, unknown) is
#: code and is skipped, because skills are never executed.
TEXT_SKILL_FORMATS: frozenset[str] = frozenset(
    {"playbook", "markdown", "md", "text", "txt"}
)


def is_text_skill(skill: dict[str, Any]) -> bool:
    """True if an imported skill is prose we can keep as a text playbook."""
    return str(skill.get("language") or "").lower() in TEXT_SKILL_FORMATS


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
    if personality.get("verbosity") is not None:
        lines.append(f"  - Verbosity: {personality['verbosity']}/10")
    if traits:
        for trait in traits[:5]:
            lines.append(f"  - {trait}")
        if len(traits) > 5:
            lines.append(f"  ... and {len(traits) - 5} more")
    lines.append("")

    # Classify memories
    sensitive = [m for m in memories if classify_memory(m) == "sensitive"]
    safe = [m for m in memories if classify_memory(m) == "safe"]
    total_memories = len(memories)

    lines.append(f"🧠 Memories found: {total_memories}")
    lines.append(f"  ✅ Safe (auto-import): {len(safe)} preferences, facts, topics")
    lines.append(f"  ⚠️  Sensitive (needs review): {len(sensitive)} beliefs, identity facts, anything like a secret")
    if skills:
        text_skills = [sk for sk in skills if is_text_skill(sk)]
        code_skills = len(skills) - len(text_skills)
        lines.append(
            f"  📘 Skills: {len(text_skills)} text playbook(s) (imported "
            "unpromoted; read, never run)"
        )
        if code_skills:
            lines.append(
                f"  ⛔ Code skills skipped: {code_skills} (Windy Fly never "
                "runs skill code)"
            )
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
    # Any type that carries something like a secret waits for review too (Hub on #586): a PIN typed
    # as a 'fact' must not be imported as safe and recalled into a prompt.
    if looks_like_secret(str(memory.get("content", ""))):
        return "sensitive"
    return "safe"


# What a password, PIN, key or account number looks like in an imported memory. It errs toward
# flagging: a false match only means the owner reviews one more item.
_SECRET_WORDS = re.compile(
    r"\b(pass(?:word|code|phrase)?|pins?|pin\s*code|api[\s_-]*keys?|secret(?:\s*key)?s?|"
    r"(?:access|auth|bearer|refresh|session)\s*tokens?|tokens?|credentials?|log\s*in\s*details|"
    r"ssn|social\s+security|account\s*(?:number|no\.?|#)|routing\s*(?:number|no\.?)|iban|"
    r"(?:credit|debit|bank)\s*card|card\s*number|cvv|cvc|security\s*code|seed\s*phrase|"
    r"recovery\s*(?:code|phrase|key)s?|private\s*key|2fa|otp)\b",
    re.IGNORECASE,
)
_SECRET_SHAPES = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|pk|rk)[-_][A-Za-z0-9_-]{16,}"),                 # sk-..., sk-ant-..., pk_live_...
    re.compile(r"\b(?:ghp|gho|ghs|github_pat|xox[abp]|AKIA)[A-Za-z0-9_-]{12,}"),
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}"),                          # a Telegram bot token
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                                # an SSN
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),                               # a card or account number
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),                      # an IBAN
    re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}\b"),  # a long random token
)


def looks_like_secret(text: str) -> bool:
    """True when ``text`` names or holds something like a password, PIN, key or account number."""
    if not text:
        return False
    return bool(_SECRET_WORDS.search(text)) or any(r.search(text) for r in _SECRET_SHAPES)
