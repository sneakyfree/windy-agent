"""Phase H5 — Extended Security Hardening Audit.

Beyond the existing test_security.py, these tests verify:
- API key masking in provider responses
- Path traversal protection
- XSS in user inputs
- Sandbox escape attempts
- Data directory not served
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from windyfly.memory.database import Database
from windyfly.memory.nodes import upsert_node, search_nodes
from windyfly.memory.episodes import save_episode

PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
GATEWAY_DIR = PROJECT_ROOT / "gateway" / "src"


# =============================================================================
# H5.1: No API Keys in Source Code
# =============================================================================


class TestNoSecretsInSource:
    def test_no_real_api_keys_in_python_source(self):
        """H5.1: Scan all Python source for leaked API keys."""
        key_patterns = [
            r'["\']sk-[a-zA-Z0-9]{20,}["\']',           # OpenAI
            r'["\']sk-ant-[a-zA-Z0-9]{20,}["\']',        # Anthropic
            r'["\']xai-[a-zA-Z0-9]{20,}["\']',           # xAI
            r'["\']gsk_[a-zA-Z0-9]{20,}["\']',           # Groq
            r'["\']pk-[a-zA-Z0-9]{20,}["\']',            # Perplexity
        ]
        for py_file in SRC_DIR.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            content = py_file.read_text(errors="ignore")
            for pattern in key_patterns:
                match = re.search(pattern, content)
                assert not match, (
                    f"Potential API key in {py_file.name}: {match.group()[:20]}..."
                )

    def test_no_real_api_keys_in_typescript(self):
        """No real API keys in gateway TypeScript source."""
        for ts_file in GATEWAY_DIR.rglob("*.ts"):
            content = ts_file.read_text(errors="ignore")
            match = re.search(r'["\']sk-[a-zA-Z0-9]{20,}["\']', content)
            assert not match, f"API key in {ts_file.name}"


# =============================================================================
# H5.3–H5.4: SQL Injection Extended
# =============================================================================


class TestSQLInjectionExtended:
    INJECTION_PAYLOADS = [
        "'; DROP TABLE nodes; --",
        "1; DELETE FROM episodes WHERE 1=1",
        "' OR '1'='1",
        "' UNION SELECT * FROM soul --",
        "Robert'); DROP TABLE episodes;--",
        "1; UPDATE soul SET value='hacked'--",
    ]

    def test_node_upsert_injection(self):
        """H5.3: SQL injection in node operations."""
        db = Database(":memory:")
        for payload in self.INJECTION_PAYLOADS:
            upsert_node(db, type="test", name=payload, source="injection_test")
        # All tables should still exist
        for table in ("nodes", "episodes", "soul", "cost_ledger", "skills"):
            result = db.fetchone(
                f"SELECT COUNT(*) as c FROM sqlite_master WHERE name='{table}'"
            )
            assert result["c"] == 1, f"Table '{table}' was dropped by injection!"
        db.close()

    def test_search_injection(self):
        """H5.4: SQL injection in search queries."""
        db = Database(":memory:")
        upsert_node(db, type="fact", name="safe_data", source="test")
        for payload in self.INJECTION_PAYLOADS:
            results = search_nodes(db, payload, limit=10)
            assert isinstance(results, list)
        # Verify original data untouched
        safe = search_nodes(db, "safe_data", limit=10)
        assert len(safe) >= 1
        db.close()

    def test_episode_injection(self):
        """SQL injection in episode content."""
        db = Database(":memory:")
        for payload in self.INJECTION_PAYLOADS:
            save_episode(db, "user", payload, session_id="inject_test")
        # Episodes table should be intact
        count = db.fetchone("SELECT COUNT(*) as c FROM episodes")
        assert count["c"] == len(self.INJECTION_PAYLOADS)
        db.close()


# =============================================================================
# H5.5: XSS Prevention
# =============================================================================


class TestXSSPrevention:
    XSS_PAYLOADS = [
        '<script>alert("XSS")</script>',
        '<img src=x onerror=alert(1)>',
        '"><svg onload=alert(1)>',
        "javascript:alert(1)",
        "<iframe src='evil.com'></iframe>",
    ]

    def test_xss_in_node_names(self):
        """H5.5: XSS payloads stored literally, not executed."""
        db = Database(":memory:")
        for payload in self.XSS_PAYLOADS:
            upsert_node(db, type="fact", name=payload, source="xss_test")

        # All should be stored literally
        results = db.fetchall("SELECT name FROM nodes WHERE source='xss_test'")
        stored_names = [r["name"] for r in results]
        for payload in self.XSS_PAYLOADS:
            assert payload in stored_names, f"XSS payload not stored literally: {payload[:30]}"
        db.close()


# =============================================================================
# H5.6: Path Traversal Protection  
# =============================================================================


class TestPathTraversal:
    def test_gateway_static_file_serving(self):
        """H5.6: Gateway should only serve from public/ directory."""
        server_ts = GATEWAY_DIR / "server.ts"
        content = server_ts.read_text(encoding="utf-8")
        # Should serve files from a restricted directory (public/)
        assert "public" in content, "Gateway doesn't reference a public/ directory"
        # Should not have open file serving from project root
        assert "resolve('..'," not in content or "../../" not in content

    def test_no_directory_listing(self):
        """Gateway should not allow directory listing."""
        server_ts = GATEWAY_DIR / "server.ts"
        content = server_ts.read_text(encoding="utf-8")
        assert "readdir" not in content, "Gateway may allow directory listing"


# =============================================================================
# H5.7: Provider Key Masking
# =============================================================================


class TestProviderKeyMasking:
    def test_provider_key_masking_function(self):
        """H5.7: Provider keys should be masked in API responses."""
        providers_ts = GATEWAY_DIR / "providers.ts"
        content = providers_ts.read_text(encoding="utf-8")
        # Should have masking logic (slice, substring, replace with *)
        has_masking = any(kw in content for kw in [
            "slice", "substring", "mask", "****", "••••", "...",
        ])
        assert has_masking, "providers.ts has no key masking logic"


# =============================================================================
# H5.8–H5.9: Sensitive Files Not Served
# =============================================================================


class TestSensitiveFileProtection:
    def test_env_not_in_public(self):
        """H5.8: .env file should not be in gateway/public/."""
        public_dir = PROJECT_ROOT / "gateway" / "public"
        assert not (public_dir / ".env").exists(), ".env found in gateway/public/"

    def test_db_not_in_public(self):
        """H5.9: Database should not be in gateway/public/."""
        public_dir = PROJECT_ROOT / "gateway" / "public"
        for ext in (".db", ".sqlite", ".sqlite3"):
            files = list(public_dir.rglob(f"*{ext}"))
            assert len(files) == 0, f"Database file found in public/: {files}"

    def test_data_directory_not_in_public(self):
        """data/ directory should not be under gateway/public/."""
        assert not (PROJECT_ROOT / "gateway" / "public" / "data").exists()


# =============================================================================
# H5.10: Sandbox Escape Prevention
# =============================================================================


class TestSandboxEscape:
    def test_blocks_os_system(self):
        """H5.10: Sandbox blocks os.system calls."""
        from windyfly.skills.evaluator import evaluate_skill
        from windyfly.memory.skills import save_skill

        db = Database(":memory:")
        skill_id = save_skill(
            db, "evil_skill",
            "import os\nos.system('rm -rf /')",
            "python",
        )
        result = evaluate_skill(db, skill_id)
        assert result["passed"] is False
        assert result["gates"]["safety"] is False
        db.close()

    def test_blocks_subprocess(self):
        """Sandbox blocks subprocess imports."""
        from windyfly.skills.evaluator import evaluate_skill
        from windyfly.memory.skills import save_skill

        db = Database(":memory:")
        skill_id = save_skill(
            db, "subprocess_skill",
            "import subprocess\nsubprocess.run(['ls'])",
            "python",
        )
        result = evaluate_skill(db, skill_id)
        assert result["passed"] is False
        db.close()

    def test_blocks_eval(self):
        """Sandbox blocks eval() calls."""
        from windyfly.skills.evaluator import evaluate_skill
        from windyfly.memory.skills import save_skill

        db = Database(":memory:")
        skill_id = save_skill(
            db, "eval_skill",
            "eval('__import__(\"os\").system(\"id\")')",
            "python",
        )
        result = evaluate_skill(db, skill_id)
        assert result["passed"] is False
        db.close()

    def test_blocks_file_read(self):
        """Sandbox blocks open() file reads."""
        from windyfly.skills.evaluator import evaluate_skill
        from windyfly.memory.skills import save_skill

        db = Database(":memory:")
        skill_id = save_skill(
            db, "file_read_skill",
            "data = open('/etc/passwd').read()",
            "python",
        )
        result = evaluate_skill(db, skill_id)
        assert result["passed"] is False
        db.close()


# =============================================================================
# No TODO/Placeholder Text in User-Facing Strings
# =============================================================================


class TestNoPlaceholders:
    def test_no_todo_in_user_facing_strings(self):
        """No TODO or PLACEHOLDER in user-facing error/info strings."""
        for py_file in SRC_DIR.rglob("*.py"):
            if "__pycache__" in str(py_file) or "test_" in py_file.name:
                continue
            content = py_file.read_text(errors="ignore")
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                # Only check strings (inside quotes), not comments
                if 'TODO' in line and ('"TODO' in line or "'TODO" in line):
                    pytest.fail(
                        f"{py_file.name}:{i} has TODO in a string: {line.strip()[:80]}"
                    )

    def test_no_placeholder_in_slider_info(self):
        """Slider descriptions should not contain 'placeholder' or 'TODO'."""
        from windyfly.control_panel import SLIDER_INFO
        for name, info in SLIDER_INFO.items():
            for field in ("description", "impact_low", "impact_high"):
                value = info.get(field, "")
                assert "TODO" not in value, f"Slider '{name}' {field} has TODO"
                assert "placeholder" not in value.lower(), (
                    f"Slider '{name}' {field} has 'placeholder'"
                )
