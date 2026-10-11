"""Skills CRUD operations.

Manages the skills table: versioned, self-improving TEXT playbooks the
agent reads (numbered steps, exact commands that worked). Skills are
never executed. Executable skills (a subprocess "sandbox" plus a regex
"safety gate") were retired on 2026-10-10: code runs only through the
agent's own tools (e.g. ``shell.exec`` in its sandbox) under the
owner's trust settings. Every write path funnels through
:func:`save_skill`, which accepts only ``language="playbook"``.

Legacy rows written before the retirement may still carry
``language`` values like ``python``; they stay as inert text.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from windyfly.memory.database import Database


#: The only skill "language" accepted on write. A skill is text the agent
#: reads, not code anything runs.
PLAYBOOK_LANGUAGE = "playbook"
ALLOWED_SKILL_LANGUAGES: frozenset[str] = frozenset({PLAYBOOK_LANGUAGE})


class SkillLanguageError(ValueError):
    """A caller tried to store a skill that is not a text playbook."""


def require_playbook_language(language: str) -> str:
    """Return ``language`` if it is an accepted skill language, else raise.

    Raises:
        SkillLanguageError: for anything other than ``"playbook"``.
    """
    if language not in ALLOWED_SKILL_LANGUAGES:
        raise SkillLanguageError(
            f"Skills are text playbooks only; language {language!r} is not "
            "accepted (use 'playbook'). Executable skills were retired: code "
            "runs only through the agent's tools, under the owner's trust "
            "settings."
        )
    return language


def is_correction_row(skill: dict[str, Any]) -> bool:
    """True for an auto-generated ``correction-*`` skill (failure_detector).

    Correction skills reach the prompt through
    :func:`get_active_correction_skills`, not the playbook index.
    """
    return str(skill.get("name") or "").startswith("correction-")


def save_skill(
    db: Database,
    name: str,
    code: str,
    language: str,
    *,
    description: str | None = None,
    permissions_required: list[str] | None = None,
    risk_level: str = "low",
    parent_skill_id: str | None = None,
) -> str:
    """Save a new skill to the database.

    Args:
        db: Database instance.
        name: Skill name.
        code: The playbook text (stored as-is, never executed).
        language: Must be ``"playbook"``; anything else is rejected.
        description: Optional human-readable description.
        permissions_required: Optional list of required permissions.
        risk_level: Risk classification ('low', 'medium', 'high').
        parent_skill_id: Optional parent skill ID for version lineage.

    Returns:
        The generated skill ID.

    Raises:
        SkillLanguageError: if ``language`` is not ``"playbook"``.
    """
    require_playbook_language(language)
    skill_id = str(uuid.uuid4())
    perms_json = json.dumps(permissions_required) if permissions_required else None

    db.execute(
        """
        INSERT INTO skills (id, name, code, language, description,
                            permissions_required, risk_level, parent_skill_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (skill_id, name, code, language, description,
         perms_json, risk_level, parent_skill_id),
    )
    db.commit()
    return skill_id


def get_skill(db: Database, skill_id: str) -> dict[str, Any] | None:
    """Get a skill by ID."""
    return db.fetchone("SELECT * FROM skills WHERE id = ?", (skill_id,))


def get_skill_by_name(db: Database, name: str) -> dict[str, Any] | None:
    """Get the most recent skill with the given name."""
    return db.fetchone(
        "SELECT * FROM skills WHERE name = ? ORDER BY version DESC LIMIT 1",
        (name,),
    )


def get_active_correction_skills(
    db: Database,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return the most recently-used promoted correction skills,
    suitable for injecting into the system prompt as "Lessons
    learned from past corrections."

    Filters:
      - ``name LIKE 'correction-%'`` (the auto-generated naming
        from ``failure_detector._build_correction_code``)
      - ``promoted = TRUE``
      - ordered by last_used DESC, then created_at DESC
      - capped at ``limit`` (default 5) to bound prompt growth

    Side effect: lazily expires stale correction skills (>30 days
    of inactivity, demoted but row kept for audit). This means a
    skill the user no longer needs eventually stops paying its
    ~100 token-per-turn cost without operator intervention.

    Why this exists: pre-2026-05-20 the agent loop never read
    skills back into the prompt — correction skills were saved on
    recurring failures but never applied. v18 e2e harness +
    grep audit caught this; PR ships the read path.
    """
    # Lazy expiry pass — cheap query against an indexed column,
    # bounded to skills past their 30-day idle horizon. Safer than
    # a separate cron because cleanup happens whenever the read
    # path is exercised.
    try:
        from windyfly.skills.manager import expire_stale_correction_skills
        expire_stale_correction_skills(db)
    except Exception:
        # Never fail the read path on an expiry hiccup; future
        # expiry attempts will catch up.
        pass
    return db.fetchall(
        """
        SELECT * FROM skills
        WHERE name LIKE 'correction-%' AND promoted = TRUE
        ORDER BY COALESCE(last_used, created_at) DESC
        LIMIT ?
        """,
        (limit,),
    )


def extract_correction_text(skill_code: str) -> str | None:
    """Pull the ``CORRECTION`` string out of a correction-skill's
    text body. The body is generated by ``_build_correction_code``
    in failure_detector.py in a Python-looking shape
    ``CORRECTION = (...)`` (stored as a text playbook). Nothing ever
    ``exec``s it; a tiny regex extracts the parenthesized string
    concatenation.

    Returns None if the code doesn't match the expected shape —
    callers should skip silently rather than crash on an
    unparseable skill.
    """
    import re
    # Match CORRECTION = ( ... ) — body is a concatenation of
    # single/double-quoted strings; collapse them into one string.
    m = re.search(r"CORRECTION\s*=\s*\(([^)]+)\)", skill_code, re.DOTALL)
    if not m:
        return None
    body = m.group(1)
    parts = re.findall(r"['\"]([^'\"]*)['\"]", body)
    if not parts:
        return None
    return "".join(parts).strip() or None


def list_skills(
    db: Database,
    *,
    promoted_only: bool = True,
) -> list[dict[str, Any]]:
    """List skills, optionally filtered to promoted-only.

    Args:
        db: Database instance.
        promoted_only: If True, only return promoted skills.

    Returns:
        List of skill dicts.
    """
    if promoted_only:
        return db.fetchall(
            "SELECT * FROM skills WHERE promoted = TRUE ORDER BY last_used DESC"
        )
    return db.fetchall("SELECT * FROM skills ORDER BY created_at DESC")
