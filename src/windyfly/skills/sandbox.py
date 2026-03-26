"""Skill sandbox — execute code in a restricted subprocess.

v1 uses subprocess with timeout and restricted environment.
v2 (future) will use Docker containers.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from typing import Any

logger = logging.getLogger(__name__)


def execute_in_sandbox(
    code: str,
    language: str,
    *,
    test_input: str | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """Execute code in a sandboxed subprocess.

    Args:
        code: Source code to execute.
        language: Programming language ('python', 'javascript', etc.).
        test_input: Optional stdin input for the code.
        timeout: Max execution time in seconds.

    Returns:
        Dict with: success, stdout, stderr, exit_code, timed_out.
    """
    if language == "python":
        return _run_python(code, test_input, timeout)
    elif language in ("javascript", "js"):
        return _run_node(code, test_input, timeout)
    else:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Unsupported language: {language}",
            "exit_code": -1,
            "timed_out": False,
        }


def _run_python(code: str, test_input: str | None, timeout: int) -> dict[str, Any]:
    """Run Python code in a subprocess."""
    restricted_env = {
        "PATH": "/usr/bin:/usr/local/bin",
        "HOME": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    try:
        result = subprocess.run(
            ["python3", "-c", code],
            input=test_input,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=restricted_env,
            cwd="/tmp",
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:10000],
            "exit_code": result.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Execution timed out after {timeout}s",
            "exit_code": -1,
            "timed_out": True,
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
            "timed_out": False,
        }


def _run_node(code: str, test_input: str | None, timeout: int) -> dict[str, Any]:
    """Run JavaScript code via Node.js."""
    try:
        result = subprocess.run(
            ["node", "-e", code],
            input=test_input,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/tmp",
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:10000],
            "exit_code": result.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Execution timed out after {timeout}s",
            "exit_code": -1,
            "timed_out": True,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "stdout": "",
            "stderr": "Node.js not found",
            "exit_code": -1,
            "timed_out": False,
        }
