"""Skills manager — create, evaluate, promote, rollback skills.

Manages the lifecycle of self-improving code snippets.
"""

from __future__ import annotations

import logging

from windyfly.memory.database import Database
from windyfly.memory.skills import get_skill, save_skill
from windyfly.memory.write_queue import Priority, WriteQueue

logger = logging.getLogger(__name__)


def create_skill(
    db: Database,
    name: str,
    code: str,
    language: str,
    description: str | None = None,
    permissions_required: list[str] | None = None,
    risk_level: str = "low",
) -> str:
    """Create a new unpromoted skill."""
    return save_skill(
        db, name, code, language,
        description=description,
        permissions_required=permissions_required,
        risk_level=risk_level,
    )


def promote_skill(db: Database, skill_id: str) -> None:
    """Promote a skill after it passes all evaluator gates.

    Args:
        db: Database instance.
        skill_id: Skill to promote.

    Raises:
        ValueError: If skill not found.
    """
    skill = get_skill(db, skill_id)
    if not skill:
        raise ValueError(f"Skill {skill_id} not found")

    db.execute(
        "UPDATE skills SET promoted = TRUE, last_used = CURRENT_TIMESTAMP WHERE id = ?",
        (skill_id,),
    )
    db.commit()
    logger.info("Promoted skill %s (%s)", skill_id, skill["name"])


def increment_usage(
    db: Database,
    write_queue: WriteQueue,
    skill_id: str,
    success: bool,
) -> None:
    """Increment usage counters for a skill via the write queue."""
    def _update():
        if success:
            db.execute(
                """UPDATE skills SET usage_count = usage_count + 1,
                   success_count = success_count + 1,
                   last_used = CURRENT_TIMESTAMP WHERE id = ?""",
                (skill_id,),
            )
        else:
            db.execute(
                """UPDATE skills SET usage_count = usage_count + 1,
                   failure_count = failure_count + 1,
                   last_used = CURRENT_TIMESTAMP WHERE id = ?""",
                (skill_id,),
            )
        db.commit()

    write_queue.enqueue(Priority.MEDIUM, _update)


def rollback_skill(db: Database, skill_id: str) -> str | None:
    """Rollback a skill to its parent version.

    Demotes the current version and promotes the parent.

    Args:
        db: Database instance.
        skill_id: Current skill to rollback.

    Returns:
        Parent skill ID or None if no parent exists.
    """
    skill = get_skill(db, skill_id)
    if not skill:
        return None

    parent_id = skill.get("parent_skill_id")
    if not parent_id:
        return None

    # Demote current
    db.execute("UPDATE skills SET promoted = FALSE WHERE id = ?", (skill_id,))
    # Promote parent
    db.execute("UPDATE skills SET promoted = TRUE WHERE id = ?", (parent_id,))
    db.commit()

    logger.info("Rolled back skill %s → parent %s", skill_id, parent_id)
    return parent_id
