"""Hardening tests for the skill system.

Executable skills (subprocess sandbox + regex safety gate) were retired
2026-10-10; the no-execution guarantees live in test_skills_text_only.py.
This file keeps the rollback handling for text playbooks.
"""

from __future__ import annotations

import pytest

from windyfly.memory.database import Database
from windyfly.memory.skills import get_skill, save_skill
from windyfly.skills.manager import create_skill, promote_skill, rollback_skill


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


# --- Skill rollback ---


class TestSkillRollback:
    def test_rollback_to_parent(self, db):
        """Rollback should demote current and promote parent."""
        parent_id = create_skill(db, "v1", "print('v1')", "playbook")
        promote_skill(db, parent_id)

        child_id = save_skill(
            db, "v2", "print('v2')", "playbook",
            parent_skill_id=parent_id,
        )
        promote_skill(db, child_id)

        result = rollback_skill(db, child_id)
        assert result == parent_id

        child = get_skill(db, child_id)
        parent = get_skill(db, parent_id)
        assert child["promoted"] in (False, 0)
        assert parent["promoted"] in (True, 1)

    def test_rollback_no_parent(self, db):
        """Rollback with no parent should return None."""
        skill_id = create_skill(db, "orphan", "print('alone')", "playbook")
        result = rollback_skill(db, skill_id)
        assert result is None

    def test_rollback_nonexistent_skill(self, db):
        """Rollback non-existent skill should return None, not crash."""
        result = rollback_skill(db, "nonexistent-id")
        assert result is None

    def test_rollback_parent_deleted(self, db):
        """Rollback when parent was deleted should handle gracefully."""
        parent_id = create_skill(db, "deleted-parent", "print('parent')", "playbook")
        child_id = save_skill(
            db, "child-of-deleted", "print('child')", "playbook",
            parent_skill_id=parent_id,
        )

        # Delete the parent
        db.execute("DELETE FROM skills WHERE id = ?", (parent_id,))
        db.commit()

        # Rollback should still work — it just promotes a non-existent parent
        # (the UPDATE will affect 0 rows, which is fine)
        result = rollback_skill(db, child_id)
        assert result == parent_id  # Returns parent_id even if parent is gone
