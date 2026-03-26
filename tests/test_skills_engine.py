"""Tests for the Skills Engine (manager, sandbox, evaluator)."""

from __future__ import annotations

import time

from windyfly.memory.database import Database
from windyfly.memory.skills import get_skill
from windyfly.memory.write_queue import WriteQueue
from windyfly.skills.evaluator import _check_safety, _check_syntax, evaluate_skill
from windyfly.skills.manager import create_skill, promote_skill, rollback_skill
from windyfly.skills.sandbox import execute_in_sandbox

import pytest


class TestSkillManager:
    def test_create_skill(self):
        db = Database(":memory:")
        sid = create_skill(db, "greet", "print('hello')", "python")
        assert sid is not None
        skill = get_skill(db, sid)
        assert skill["name"] == "greet"
        assert skill["promoted"] == 0  # Not promoted
        db.close()

    def test_promote_skill(self):
        db = Database(":memory:")
        sid = create_skill(db, "greet", "print('hello')", "python")
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
        parent_id = create_skill(db, "calc", "print(1+1)", "python")
        promote_skill(db, parent_id)
        child_id = create_skill(
            db, "calc_v2", "print(2+2)", "python",
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
        sid = create_skill(db, "solo", "pass", "python")
        result = rollback_skill(db, sid)
        assert result is None
        db.close()


class TestSandbox:
    def test_run_python_success(self):
        result = execute_in_sandbox("print('hello')", "python")
        assert result["success"] is True
        assert "hello" in result["stdout"]
        assert result["timed_out"] is False

    def test_run_python_error(self):
        result = execute_in_sandbox("raise ValueError('oops')", "python")
        assert result["success"] is False
        assert result["exit_code"] != 0

    def test_run_python_timeout(self):
        result = execute_in_sandbox("import time; time.sleep(20)", "python", timeout=1)
        assert result["timed_out"] is True

    def test_unsupported_language(self):
        result = execute_in_sandbox("code", "rust")
        assert result["success"] is False
        assert "Unsupported" in result["stderr"]


class TestEvaluator:
    def test_syntax_check_valid(self):
        result = _check_syntax("print('hello')", "python")
        assert result["passed"] is True

    def test_syntax_check_invalid(self):
        result = _check_syntax("def broken(:", "python")
        assert result["passed"] is False

    def test_safety_clean_code(self):
        result = _check_safety("x = 1 + 2\nprint(x)", [])
        assert result["passed"] is True
        assert len(result["violations"]) == 0

    def test_safety_banned_import(self):
        result = _check_safety("import os\nos.system('ls')", [])
        assert result["passed"] is False
        assert len(result["violations"]) >= 1

    def test_safety_with_permission(self):
        # If the banned pattern is in permissions, it should pass
        result = _check_safety("import os", [r"import\s+os"])
        assert result["passed"] is True

    def test_evaluate_full_pass(self):
        db = Database(":memory:")
        sid = create_skill(db, "hello", "print('hi')", "python")
        result = evaluate_skill(db, sid)
        assert result["passed"] is True
        assert all(result["gates"].values())
        db.close()

    def test_evaluate_syntax_fail(self):
        db = Database(":memory:")
        sid = create_skill(db, "broken", "def bad(:", "python")
        result = evaluate_skill(db, sid)
        assert result["passed"] is False
        assert result["gates"]["syntax"] is False
        db.close()

    def test_evaluate_safety_fail(self):
        db = Database(":memory:")
        sid = create_skill(db, "dangerous", "import subprocess\nsubprocess.run(['ls'])", "python")
        result = evaluate_skill(db, sid)
        assert result["passed"] is False
        db.close()
