"""Skills CRUD operations.

Manages the skills table — versioned, self-improving code snippets
that the agent can create, evaluate, and promote.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from windyfly.memory.database import Database


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
        code: The skill source code.
        language: Programming language ('python', 'javascript', etc.).
        description: Optional human-readable description.
        permissions_required: Optional list of required permissions.
        risk_level: Risk classification ('low', 'medium', 'high').
        parent_skill_id: Optional parent skill ID for version lineage.

    Returns:
        The generated skill ID.
    """
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
