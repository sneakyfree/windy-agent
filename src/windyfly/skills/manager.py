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


def demote_skill(db: Database, skill_id: str) -> bool:
    """Mark a skill as not-promoted (history kept). Used by the
    user-facing ``/forget`` slash command when an auto-promoted
    correction skill turns out to be bad advice, and by the
    automatic expiry path when a correction skill hasn't been
    needed for N successful turns.

    Returns True iff a row was actually updated (False = skill_id
    not found; caller should surface "no such skill").
    """
    skill = get_skill(db, skill_id)
    if not skill:
        return False
    db.execute(
        "UPDATE skills SET promoted = FALSE WHERE id = ?",
        (skill_id,),
    )
    db.commit()
    logger.info("Demoted skill %s (%s)", skill_id, skill["name"])
    return True


def expire_stale_correction_skills(
    db: Database,
    *,
    max_age_days: int = 30,
) -> int:
    """Demote promoted ``correction-*`` skills whose ``last_used``
    is older than ``max_age_days``. Returns the count demoted.

    Rationale: a correction skill exists because the user kept
    making the same fault. Once that fault stops recurring (user
    learned, topic moved on, etc.), the skill should stop being
    injected into every prompt — keeping it active forever means
    paying 100 tokens per turn for advice the user no longer
    needs.

    ``last_used`` is set at promotion time and re-bumped when
    handle_friction creates a NEW skill of the same fault_type
    (recurring detection). So a skill that hasn't been re-touched
    in 30 days = the fault_type has been quiet for 30 days = safe
    to retire from active rotation. The row stays for audit + can
    be re-promoted via the bridge UDS server if it turns out the
    pattern returns.

    Called lazily from ``get_active_correction_skills`` so cleanup
    happens on read without a separate cron. Bounded query — no
    table scan over inactive skills.
    """
    rows = db.fetchall(
        """
        SELECT id FROM skills
        WHERE name LIKE 'correction-%' AND promoted = TRUE
          AND (
            last_used < datetime('now', ?)
            OR (last_used IS NULL AND created_at < datetime('now', ?))
          )
        """,
        (f"-{max_age_days} days", f"-{max_age_days} days"),
    )
    n = 0
    for r in rows:
        if demote_skill(db, r["id"]):
            n += 1
    if n:
        logger.info(
            "Expired %d stale correction skill(s) (>%dd inactive)",
            n, max_age_days,
        )
    return n


def demote_skill_by_name(db: Database, name_substring: str) -> list[dict]:
    """User-facing demote that matches by name substring (the user
    types ``/forget factual_error`` rather than a UUID). Returns
    the list of demoted skill rows so the caller can confirm what
    was actually affected — handles the common case where the user
    types a fault-type and we demote ALL matching correction
    skills.

    Matches are case-insensitive and SQL-LIKE-shaped (no regex);
    safer for a freeform user input than trusting them with full
    LIKE wildcards.
    """
    safe = name_substring.lower().strip()
    if not safe:
        return []
    rows = db.fetchall(
        "SELECT * FROM skills WHERE LOWER(name) LIKE ? AND promoted = TRUE",
        (f"%{safe}%",),
    )
    for r in rows:
        demote_skill(db, r["id"])
    return rows


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
