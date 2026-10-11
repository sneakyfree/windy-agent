"""Tests for the Skills Engine manager (text playbooks; nothing executes)."""

from __future__ import annotations

from windyfly.memory.database import Database
from windyfly.memory.skills import get_skill
from windyfly.skills.manager import create_skill, promote_skill, rollback_skill

import pytest


class TestSkillManager:
    def test_create_skill(self):
        db = Database(":memory:")
        sid = create_skill(db, "greet", "print('hello')", "playbook")
        assert sid is not None
        skill = get_skill(db, sid)
        assert skill["name"] == "greet"
        assert skill["promoted"] == 0  # Not promoted
        db.close()

    def test_promote_skill(self):
        db = Database(":memory:")
        sid = create_skill(db, "greet", "print('hello')", "playbook")
        promote_skill(db, sid)
        skill = get_skill(db, sid)
        assert skill["promoted"] == 1
        db.close()

    def test_promote_nonexistent(self):
        db = Database(":memory:")
        with pytest.raises(ValueError):
            promote_skill(db, "nonexistent")
        db.close()

    def test_rollback_with_parent(self):
        db = Database(":memory:")
        parent_id = create_skill(db, "calc", "print(1+1)", "playbook")
        promote_skill(db, parent_id)
        child_id = create_skill(
            db, "calc_v2", "print(2+2)", "playbook",
        )
        # Manually set parent
        db.execute("UPDATE skills SET parent_skill_id = ? WHERE id = ?", (parent_id, child_id))
        db.commit()
        promote_skill(db, child_id)

        result = rollback_skill(db, child_id)
        assert result == parent_id

        child = get_skill(db, child_id)
        parent = get_skill(db, parent_id)
        assert child["promoted"] == 0
        assert parent["promoted"] == 1
        db.close()

    def test_rollback_no_parent(self):
        db = Database(":memory:")
        sid = create_skill(db, "solo", "pass", "playbook")
        result = rollback_skill(db, sid)
        assert result is None
        db.close()
