"""Skill sandbox — execute code in a restricted subprocess.

v1 uses subprocess with timeout and restricted environment.
v2 (future) will use Docker containers.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from typing import Any

logger = logging.getLogger(__name__)


def _sandbox_cwd() -> str:
    """A scratch working directory that exists on every platform.

    Was the literal ``"/tmp"``, which does not exist on Windows.
    """
    return tempfile.gettempdir()


def _restricted_env() -> dict[str, str]:
    """Minimal environment for skill execution.

    Keeps the v1 intent — hand the child as little ambient state as
    possible — without assuming a POSIX filesystem. The previous
    version hardcoded ``PATH=/usr/bin:/usr/local/bin`` and
    ``HOME=/tmp``, both meaningless on Windows.

    ``SystemRoot`` is not optional on Windows: omit it and the Windows
    socket/DLL loader fails in ways that surface as unrelated errors.
    """
    tmp = _sandbox_cwd()
    if os.name == "nt":
        return {
            "PATH": os.environ.get("PATH", ""),
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "TEMP": tmp,
            "TMP": tmp,
            "USERPROFILE": tmp,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    return {
        "PATH": "/usr/bin:/usr/local/bin",
        "HOME": tmp,
        "PYTHONDONTWRITEBYTECODE": "1",
    }


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
    from windyfly.trust.gate import TrustDenied, require_trust_sync
    try:
        require_trust_sync("run_command")
    except TrustDenied as denied:
        logger.warning("Sandbox execution blocked by trust gate: %s", denied)
        return {
            "success": False,
            "stdout": "",
            "stderr": str(denied),
            "exit_code": -1,
            "timed_out": False,
            "denied": True,
        }

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
    """Run Python code in a subprocess.

    Uses ``sys.executable`` rather than the string ``"python3"``.

    On Windows, bare ``python``/``python3`` resolves to the Microsoft
    Store **App Execution Alias** — a stub that is not an interpreter.
    It exits 9009 with "Python was not found; run without arguments to
    install from the Microsoft Store", so EVERY skill execution failed
    on Windows and the whole self-learned-skills system was dead on the
    most common desktop OS. Measured on the GrantW Windows 11 box:
    11 of 26 remaining suite failures traced to this single string.

    ``sys.executable`` is also strictly more correct everywhere else —
    it runs the SAME interpreter the agent is running, instead of
    whatever ``python3`` happens to mean on that PATH.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            input=test_input,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_restricted_env(),
            cwd=_sandbox_cwd(),
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
            cwd=_sandbox_cwd(),
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
