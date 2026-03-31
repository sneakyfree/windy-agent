"""Hardening tests for the skill system security.

Tests safety gate rejection of dangerous code, sandbox timeouts,
execution failures, and rollback handling.
"""

from __future__ import annotations

import pytest

from windyfly.memory.database import Database
from windyfly.memory.skills import get_skill, save_skill
from windyfly.memory.write_queue import WriteQueue
from windyfly.skills.evaluator import BANNED_PATTERNS, evaluate_skill, _check_safety
from windyfly.skills.manager import create_skill, promote_skill, rollback_skill
from windyfly.skills.sandbox import execute_in_sandbox


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture
def wq():
    return WriteQueue()


# --- Safety gate: banned patterns ---


class TestSafetyGate:
    def test_import_os_rejected(self, db):
        """Skill with 'import os' should fail safety gate."""
        skill_id = create_skill(db, "evil1", "import os\nos.system('rm -rf /')", "python")
        result = evaluate_skill(db, skill_id)
        assert result["passed"] is False
        assert result["gates"]["safety"] is False

    def test_exec_rejected(self, db):
        """Skill with 'exec()' should fail safety gate."""
        skill_id = create_skill(db, "evil2", "exec('print(1)')", "python")
        result = evaluate_skill(db, skill_id)
        assert result["passed"] is False

    def test_import_subprocess_rejected(self, db):
        """Skill with 'import subprocess' should fail safety gate."""
        skill_id = create_skill(db, "evil3", "import subprocess\nsubprocess.run(['ls'])", "python")
        result = evaluate_skill(db, skill_id)
        assert result["passed"] is False

    def test_eval_rejected(self, db):
        """Skill with 'eval()' should fail safety gate."""
        skill_id = create_skill(db, "evil4", "result = eval('1+1')", "python")
        result = evaluate_skill(db, skill_id)
        assert result["passed"] is False

    def test_dunder_import_rejected(self, db):
        """Skill with '__import__' should fail safety gate."""
        skill_id = create_skill(db, "evil5", "__import__('os').system('ls')", "python")
        result = evaluate_skill(db, skill_id)
        assert result["passed"] is False

    def test_rm_rf_rejected(self, db):
        """Skill containing 'rm -rf' should fail safety gate."""
        result = _check_safety("# rm -rf /home/user", [])
        assert result["passed"] is False

    def test_safe_code_passes(self, db):
        """Safe Python code should pass all gates."""
        skill_id = create_skill(db, "safe", "print('hello world')", "python")
        result = evaluate_skill(db, skill_id)
        assert result["passed"] is True
        assert all(result["gates"].values())

    def test_safety_check_with_permissions(self):
        """Banned pattern with matching permission should pass."""
        # If open() is in permissions_required, it should be allowed
        code = "with open('file.txt') as f: pass"
        result = _check_safety(code, [r"open\s*\("])
        assert result["passed"] is True


# --- Sandbox execution ---


class TestSandboxExecution:
    def test_infinite_loop_timeout(self):
        """Infinite loop should timeout at 10 second limit."""
        result = execute_in_sandbox("while True: pass", "python", timeout=2)
        assert result["timed_out"] is True
        assert result["success"] is False

    def test_syntax_error_caught(self):
        """Syntax error should be caught, not crash the sandbox."""
        result = execute_in_sandbox("def broken(:", "python")
        assert result["success"] is False
        assert result["stderr"] != ""

    def test_runtime_error_caught(self):
        """Runtime error should be caught."""
        result = execute_in_sandbox("1/0", "python")
        assert result["success"] is False
        assert "ZeroDivision" in result["stderr"]

    def test_successful_execution(self):
        """Valid code should execute successfully."""
        result = execute_in_sandbox("print('hello')", "python")
        assert result["success"] is True
        assert "hello" in result["stdout"]

    def test_output_captured(self):
        """stdout and stderr should be captured."""
        result = execute_in_sandbox(
            "import sys; print('out'); print('err', file=sys.stderr)",
            "python",
        )
        assert "out" in result["stdout"]
        assert "err" in result["stderr"]

    def test_large_output_capped(self):
        """Output should be capped (sandbox limits to 10k chars)."""
        result = execute_in_sandbox("print('A' * 100000)", "python")
        assert result["success"] is True
        assert len(result["stdout"]) <= 10_001  # 10k + newline


# --- Skill evaluation end-to-end ---


class TestSkillEvaluation:
    def test_nonexistent_skill(self, db):
        """Evaluating non-existent skill should return failed, not crash."""
        result = evaluate_skill(db, "nonexistent-id")
        assert result["passed"] is False
        assert "not found" in result["details"]

    def test_syntax_error_fails_early(self, db):
        """Syntax error should fail at gate 1, not proceed to execution."""
        skill_id = create_skill(db, "bad-syntax", "def broken(:", "python")
        result = evaluate_skill(db, skill_id)
        assert result["passed"] is False
        assert result["gates"]["syntax"] is False
        assert result["gates"]["execution"] is False  # Never reached
        assert result["gates"]["safety"] is False  # Never reached

    def test_execution_fail_doesnt_promote(self, db):
        """Skill that passes syntax but fails execution should not be promoted."""
        skill_id = create_skill(db, "runtime-fail", "raise RuntimeError('boom')", "python")
        result = evaluate_skill(db, skill_id)
        assert result["passed"] is False
        assert result["gates"]["syntax"] is True
        assert result["gates"]["execution"] is False

        # Should not be promoted
        skill = get_skill(db, skill_id)
        assert skill["promoted"] is False or skill["promoted"] == 0


# --- Skill rollback ---


class TestSkillRollback:
    def test_rollback_to_parent(self, db):
        """Rollback should demote current and promote parent."""
        parent_id = create_skill(db, "v1", "print('v1')", "python")
        promote_skill(db, parent_id)

        child_id = save_skill(
            db, "v2", "print('v2')", "python",
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
        skill_id = create_skill(db, "orphan", "print('alone')", "python")
        result = rollback_skill(db, skill_id)
        assert result is None

    def test_rollback_nonexistent_skill(self, db):
        """Rollback non-existent skill should return None, not crash."""
        result = rollback_skill(db, "nonexistent-id")
        assert result is None

    def test_rollback_parent_deleted(self, db):
        """Rollback when parent was deleted should handle gracefully."""
        parent_id = create_skill(db, "deleted-parent", "print('parent')", "python")
        child_id = save_skill(
            db, "child-of-deleted", "print('child')", "python",
            parent_skill_id=parent_id,
        )

        # Delete the parent
        db.execute("DELETE FROM skills WHERE id = ?", (parent_id,))
        db.commit()

        # Rollback should still work — it just promotes a non-existent parent
        # (the UPDATE will affect 0 rows, which is fine)
        result = rollback_skill(db, child_id)
        assert result == parent_id  # Returns parent_id even if parent is gone
