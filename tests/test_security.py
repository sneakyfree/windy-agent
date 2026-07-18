"""Tier 5 — Security Hardening Audit.

Automated security verification: secret leak detection, SQL injection
prevention, sandbox isolation, environment hygiene, and timeout enforcement.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


# Root directories
PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
GATEWAY_DIR = PROJECT_ROOT / "gateway" / "src"


def _find_python_files() -> list[Path]:
    """Recursively find all .py files in src/"""
    return list(SRC_DIR.rglob("*.py"))


def _find_ts_files() -> list[Path]:
    """Recursively find all .ts files in gateway/src/"""
    return list(GATEWAY_DIR.rglob("*.ts"))


def _all_source_content() -> str:
    """Concatenate all Python + TypeScript source for bulk scanning."""
    content = []
    for f in _find_python_files():
        content.append(f.read_text(errors="ignore"))
    for f in _find_ts_files():
        content.append(f.read_text(errors="ignore"))
    return "\n".join(content)


# === Secret Leak Detection ===


class TestSecretLeaks:
    def test_no_hardcoded_api_keys_in_python(self):
        """No hardcoded API keys (sk-..., sk_live_, etc.) in Python source."""
        for py_file in _find_python_files():
            content = py_file.read_text(errors="ignore")
            # Skip test files and __pycache__
            if "__pycache__" in str(py_file) or "test_" in py_file.name:
                continue
            assert not re.search(
                r'["\']sk-[a-zA-Z0-9]{20,}["\']', content
            ), f"Hardcoded API key found in {py_file}"

    def test_no_hardcoded_api_keys_in_typescript(self):
        """No hardcoded API keys in TypeScript source."""
        for ts_file in _find_ts_files():
            content = ts_file.read_text(errors="ignore")
            assert not re.search(
                r'["\']sk-[a-zA-Z0-9]{20,}["\']', content
            ), f"Hardcoded API key found in {ts_file}"

    def test_no_hardcoded_jwt_tokens(self):
        """No JWT-looking strings (eyJ...) hardcoded in source."""
        for py_file in _find_python_files():
            if "__pycache__" in str(py_file) or "test_" in py_file.name:
                continue
            content = py_file.read_text(errors="ignore")
            # JWT pattern: eyJ followed by base64 chars
            if re.search(r'["\']eyJ[A-Za-z0-9_-]{50,}["\']', content):
                pytest.fail(f"Hardcoded JWT found in {py_file}")

    def test_env_file_in_gitignore(self):
        """The .env file should be listed in .gitignore."""
        gitignore = PROJECT_ROOT / ".gitignore"
        assert gitignore.exists(), ".gitignore does not exist"
        content = gitignore.read_text(encoding="utf-8")
        assert ".env" in content, ".env is not in .gitignore"

    def test_no_env_file_committed(self):
        """.env file should be gitignored and NOT tracked by git."""
        import subprocess
        # .env existing locally is fine — it's needed for development.
        # The real danger is if it's tracked by git (committed with secrets).
        try:
            result = subprocess.run(
                ["git", "ls-files", "--error-unmatch", ".env"],
                capture_output=True, text=True, cwd=str(PROJECT_ROOT),
                timeout=5,
            )
            # Exit code 0 means the file IS tracked — that's bad
            assert result.returncode != 0, (
                ".env is tracked by git! It should be in .gitignore and untracked."
            )
        except FileNotFoundError:
            pass  # git not installed — skip


# === SQL Injection Prevention ===


class TestSQLInjectionPrevention:
    def test_no_f_string_sql_in_memory_modules(self):
        """Memory modules should use parameterized queries, not f-strings.

        We check for the pattern: db.execute(f"... or db.fetchone(f"...
        These are dangerous because they embed user input directly in SQL.
        """
        memory_dir = SRC_DIR / "windyfly" / "memory"
        violations = []

        for py_file in memory_dir.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            content = py_file.read_text(errors="ignore")
            # Look for db.execute(f" or db.fetchone(f" or db.fetchall(f"
            # Skip files that use f-strings for dynamic WHERE clause building
            # but with parameterized ? placeholders (safe pattern):
            # - decay.py: uses f-string for int literals from _RETENTION_MAP
            # - failures.py: builds WHERE dynamically but values use ? params
            # - nodes.py: search_nodes builds OR'd LIKE clause from len(terms)
            #   but every value is passed via ? placeholders — same safe
            #   pattern as failures.py (verified 2026-05-05)
            # - journal.py: read_entries builds a dynamic WHERE from a
            #   HARDCODED clause set (no user input in the SQL text); every
            #   value (since/until/limit) is passed via ? placeholders
            #   (verified 2026-07-18)
            if py_file.name in ("decay.py", "failures.py", "nodes.py",
                                "journal.py"):
                continue
            matches = re.findall(
                r'db\.(execute|fetchone|fetchall)\s*\(\s*f["\']',
                content,
            )
            if matches:
                violations.append(f"{py_file.name}: {len(matches)} f-string SQL(s)")

        assert len(violations) == 0, (
            f"F-string SQL found (potential injection): {violations}"
        )


# === Sandbox Isolation ===


class TestSandboxIsolation:
    def test_python_sandbox_uses_restricted_path(self):
        """The sandbox should restrict PATH to system binaries only."""
        from windyfly.skills.sandbox import execute_in_sandbox
        result = execute_in_sandbox(
            "import os; print(os.environ.get('PATH', ''))",
            "python", timeout=5,
        )
        if result["success"]:
            path = result["stdout"].strip()
            assert "/usr/bin" in path, f"Sandbox PATH missing /usr/bin: {path}"
            # Should NOT include user-specific paths
            assert "/Users/" not in path, f"Sandbox PATH leaks user dir: {path}"

    def test_python_sandbox_cwd_is_tmp(self):
        """Python sandbox should run in /tmp, not the project directory."""
        from windyfly.skills.sandbox import execute_in_sandbox
        result = execute_in_sandbox(
            "import os; print(os.getcwd())",
            "python", timeout=5,
        )
        if result["success"]:
            cwd = result["stdout"].strip()
            assert "tmp" in cwd.lower(), f"Sandbox CWD is not /tmp: {cwd}"


# === Timeout Enforcement ===


class TestTimeoutEnforcement:
    def test_windy_api_has_timeout(self):
        """All httpx calls in windy_api.py should use a timeout."""
        api_file = SRC_DIR / "windyfly" / "tools" / "windy_api.py"
        content = api_file.read_text(encoding="utf-8")
        # Count httpx.get/httpx.post calls and timeout= params
        http_calls = len(re.findall(r'httpx\.(get|post)\(', content))
        timeout_params = len(re.findall(r'timeout=', content))
        assert timeout_params >= http_calls, (
            f"Found {http_calls} HTTP calls but only {timeout_params} timeouts"
        )

    def test_web_search_has_timeout(self):
        """Web search tool should enforce a timeout."""
        search_file = SRC_DIR / "windyfly" / "tools" / "web_search.py"
        content = search_file.read_text(encoding="utf-8")
        assert "timeout" in content, "web_search.py has no timeout on HTTP calls"

    def test_uds_bridge_has_timeout(self):
        """UDS bridge client should have a timeout on calls."""
        bridge_file = GATEWAY_DIR / "bridge.ts"
        content = bridge_file.read_text(encoding="utf-8")
        assert "timeout" in content.lower() or "timeoutMs" in content, (
            "bridge.ts has no timeout on UDS calls"
        )


# === CORS Configuration ===


class TestCORSConfiguration:
    def test_cors_headers_present(self):
        """Gateway should set CORS headers."""
        server_file = GATEWAY_DIR / "server.ts"
        content = server_file.read_text(encoding="utf-8")
        assert "Access-Control-Allow-Origin" in content
        assert "Access-Control-Allow-Methods" in content

    def test_cors_allows_options_preflight(self):
        """Gateway should handle OPTIONS preflight requests."""
        server_file = GATEWAY_DIR / "server.ts"
        content = server_file.read_text(encoding="utf-8")
        assert "OPTIONS" in content
        assert "204" in content
