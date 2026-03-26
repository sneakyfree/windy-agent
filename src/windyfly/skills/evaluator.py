"""Skill evaluator — 3-gate evaluation pipeline.

Gate 1: Syntax (compile/parse)
Gate 2: Execution (sandbox run)
Gate 3: Safety (banned pattern scan)
"""

from __future__ import annotations

import re
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.skills import get_skill
from windyfly.skills.sandbox import execute_in_sandbox

BANNED_PATTERNS: list[str] = [
    r"import\s+os",
    r"import\s+subprocess",
    r"import\s+shutil",
    r"open\s*\(",
    r"exec\s*\(",
    r"eval\s*\(",
    r"__import__",
    r"rm\s+-rf",
    r"curl\s+",
    r"wget\s+",
]


def evaluate_skill(db: Database, skill_id: str) -> dict[str, Any]:
    """Evaluate a skill through 3 gates.

    Args:
        db: Database instance.
        skill_id: Skill to evaluate.

    Returns:
        Dict with: passed (bool), gates (dict), details (str).
    """
    skill = get_skill(db, skill_id)
    if not skill:
        return {
            "passed": False,
            "gates": {"syntax": False, "execution": False, "safety": False},
            "details": f"Skill {skill_id} not found",
        }

    code = skill["code"]
    language = skill["language"]
    permissions = skill.get("permissions_required") or []
    if isinstance(permissions, str):
        import json
        try:
            permissions = json.loads(permissions)
        except (json.JSONDecodeError, TypeError):
            permissions = []

    gates: dict[str, bool] = {"syntax": False, "execution": False, "safety": False}

    # Gate 1: Syntax
    syntax_result = _check_syntax(code, language)
    gates["syntax"] = syntax_result["passed"]
    if not gates["syntax"]:
        return {
            "passed": False,
            "gates": gates,
            "details": f"Syntax error: {syntax_result['error']}",
        }

    # Gate 2: Execution
    exec_result = execute_in_sandbox(code, language, timeout=10)
    gates["execution"] = exec_result["success"]
    if not gates["execution"]:
        return {
            "passed": False,
            "gates": gates,
            "details": f"Execution failed: {exec_result['stderr'][:200]}",
        }

    # Gate 3: Safety
    safety_result = _check_safety(code, permissions)
    gates["safety"] = safety_result["passed"]
    if not gates["safety"]:
        return {
            "passed": False,
            "gates": gates,
            "details": f"Safety violation: {safety_result['violations']}",
        }

    return {
        "passed": True,
        "gates": gates,
        "details": "All gates passed",
    }


def _check_syntax(code: str, language: str) -> dict[str, Any]:
    """Gate 1: Check code syntax."""
    if language == "python":
        try:
            compile(code, "<skill>", "exec")
            return {"passed": True, "error": None}
        except SyntaxError as e:
            return {"passed": False, "error": str(e)}
    # Other languages: assume syntax is OK (sandbox will catch errors)
    return {"passed": True, "error": None}


def _check_safety(
    code: str,
    permissions: list[str],
) -> dict[str, Any]:
    """Gate 3: Scan for banned patterns."""
    violations: list[str] = []

    for pattern in BANNED_PATTERNS:
        if re.search(pattern, code):
            # Check if this pattern is in the permitted list
            if pattern not in permissions:
                violations.append(pattern)

    return {
        "passed": len(violations) == 0,
        "violations": violations,
    }
