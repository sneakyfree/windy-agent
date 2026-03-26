"""Golden test runner — automated regression testing for skills.

Runs stored golden tests (input/expected pairs) against promoted skills
to catch regressions.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.skills import get_skill, list_skills
from windyfly.skills.sandbox import execute_in_sandbox

logger = logging.getLogger(__name__)


def run_golden_tests(db: Database, skill_id: str) -> dict[str, Any]:
    """Run golden tests for a specific skill.

    Golden tests are stored in the skill's eval_results JSON field:
    { "golden_tests": [{ "input": "...", "expected_output": "..." }] }

    Args:
        db: Database instance.
        skill_id: Skill to test.

    Returns:
        Dict with passed, failed, total, and individual results.
    """
    skill = get_skill(db, skill_id)
    if not skill:
        return {"passed": 0, "failed": 0, "total": 0, "results": [], "error": "Skill not found"}

    eval_results = skill.get("eval_results")
    if not eval_results:
        return {"passed": 0, "failed": 0, "total": 0, "results": [], "error": "No golden tests defined"}

    try:
        data = json.loads(eval_results) if isinstance(eval_results, str) else eval_results
    except (json.JSONDecodeError, TypeError):
        return {"passed": 0, "failed": 0, "total": 0, "results": [], "error": "Invalid eval_results JSON"}

    tests = data.get("golden_tests", [])
    if not tests:
        return {"passed": 0, "failed": 0, "total": 0, "results": [], "error": "No golden tests found"}

    passed = 0
    failed = 0
    results: list[dict[str, Any]] = []

    for i, test in enumerate(tests):
        test_input = test.get("input", "")
        expected = test.get("expected_output", "")

        exec_result = execute_in_sandbox(
            skill["code"],
            skill["language"],
            test_input=test_input,
            timeout=5,
        )

        actual_output = exec_result["stdout"].strip()
        # Fuzzy match: check if expected is contained in actual
        match = expected.strip() in actual_output or actual_output == expected.strip()

        if match and exec_result["success"]:
            passed += 1
            results.append({"test": i + 1, "passed": True, "expected": expected, "actual": actual_output})
        else:
            failed += 1
            results.append({
                "test": i + 1,
                "passed": False,
                "expected": expected,
                "actual": actual_output,
                "stderr": exec_result["stderr"][:200] if exec_result["stderr"] else None,
            })

    return {"passed": passed, "failed": failed, "total": len(tests), "results": results}


def run_regression_suite(db: Database) -> dict[str, Any]:
    """Run golden tests for all promoted skills.

    Args:
        db: Database instance.

    Returns:
        Dict with overall results and any regressions.
    """
    skills = list_skills(db, promoted_only=True)
    regressions: list[dict[str, Any]] = []
    total_passed = 0
    total_failed = 0

    for skill in skills:
        result = run_golden_tests(db, skill["id"])
        total_passed += result["passed"]
        total_failed += result["failed"]

        if result["failed"] > 0:
            regressions.append({
                "skill_name": skill["name"],
                "skill_id": skill["id"],
                "failed_tests": [r for r in result["results"] if not r["passed"]],
            })

    return {
        "total_skills_tested": len(skills),
        "total_passed": total_passed,
        "total_failed": total_failed,
        "regressions": regressions,
        "has_regressions": len(regressions) > 0,
    }
