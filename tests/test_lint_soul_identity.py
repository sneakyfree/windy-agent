"""The soul/identity lint (.github/lint/lint-soul-identity.py): a PUBLIC generic repo must not gain a real agent's
soul file, passport id, agent Matrix id, instance [agent] name or denylisted name (Boss repo review 2026-11-06).

Placeholders below are built from parts so this test file never holds a real-looking id itself.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / ".github" / "lint" / "lint-soul-identity.py"
_spec = importlib.util.spec_from_file_location("lint_soul_identity", _PATH)
lint = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(lint)

REAL_PP = "ET26-" + "AB12-CD34"                 # shaped like a real passport (not TEST, not XXXX)
REAL_MXID = "@agent_et26-" + "ab12-cd34:chat.example"
DENY = ["Example Owner", "Agent Zed"]


class TestSoulFiles:
    @pytest.mark.parametrize("path", ["SOUL.md"])
    def test_the_repo_default_soul_is_allowed(self, path: str) -> None:
        assert lint.soul_file_problem(path) is None

    @pytest.mark.parametrize("path", ["docs/SOUL.md", "x/identity.md", "ECOSYSTEM.md", "a/zed.soul.md",
                                      "souls/zed/notes.md", "windy-0-soul/config.toml", "soul/readme.txt"])
    def test_any_other_soul_file_fails(self, path: str) -> None:
        assert lint.soul_file_problem(path)

    @pytest.mark.parametrize("path", ["src/windyfly/soul_import/flow.py", "tests/test_soul_import.py",
                                      "src/windyfly/personality/engine.py", "docs/design/x.md"])
    def test_code_about_souls_is_fine(self, path: str) -> None:
        assert lint.soul_file_problem(path) is None


class TestAddedLines:
    def test_a_real_passport_or_mxid_fails_anywhere(self) -> None:
        assert lint.line_problems("docs/a.md", f"agent {REAL_PP} said", DENY)
        assert lint.line_problems("src/x.py", f"room for {REAL_MXID}", DENY)

    @pytest.mark.parametrize("line", ["ET26-TEST-AB12 lab", "ET26-XXXX-XXXX placeholder", "@agent_et26-test-ab12:x",
                                      "@agent_et26-xxxx-xxxx:x"])
    def test_test_ids_and_placeholders_pass(self, line: str) -> None:
        assert lint.line_problems("docs/a.md", line, DENY) == []

    def test_the_allow_marker_skips_a_fixture_line(self) -> None:
        assert lint.line_problems("tests/t.py", f"x = '{REAL_PP}'  # soul-lint: allow", DENY) == []

    def test_a_denylisted_name_fails_only_where_identity_ships(self) -> None:
        assert lint.line_problems("SOUL.md", "You are Agent Zed.", DENY)
        assert lint.line_problems("src/windyfly/personality/engine.py", "'example owner'", DENY)
        assert lint.line_problems("a/b/config.toml", "owner = 'Example Owner'", DENY)
        # TODO lines, changelogs and code comments elsewhere may name people: not this lint's business
        assert lint.line_problems("TODO.md", "waiting on Example Owner", DENY) == []
        assert lint.line_problems("src/windyfly/agent/loop.py", "# the Agent Zed fix", DENY) == []


class TestTomlAgentName:
    def test_a_real_agent_name_on_an_added_line_fails(self) -> None:
        toml = '[agent]\nname = "Agent Zed"\n'
        assert lint.toml_agent_name_problem(toml, ['name = "Agent Zed"'])

    @pytest.mark.parametrize("name", ["Windy Fly", "windy  fly", "HiFly"])
    def test_the_product_default_passes(self, name: str) -> None:
        toml = f'[agent]\nname = "{name}"\n'
        assert lint.toml_agent_name_problem(toml, [f'name = "{name}"']) is None

    def test_an_unchanged_name_line_is_not_flagged(self) -> None:
        toml = '[agent]\nname = "Agent Zed"\n[memory]\ndb_path = "x"\n'
        assert lint.toml_agent_name_problem(toml, ['db_path = "x"']) is None

    def test_a_name_outside_the_agent_table_is_ignored(self) -> None:
        toml = '[project]\nname = "windyfly"\n'
        assert lint.toml_agent_name_problem(toml, ['name = "windyfly"']) is None


def test_the_shipped_denylist_holds_public_names_only() -> None:
    deny = (_PATH.parent / "soul-identity-denylist.txt").read_text(encoding="utf-8")
    names = [x for x in deny.splitlines() if x.strip() and not x.startswith("#")]
    assert 1 <= len(names) <= 10
    assert not any("@" in n or any(c.isdigit() for c in n.replace("Windy 0", "")) for n in names)


class TestDiffParsing:
    """Hub on #747: hunks are read by their counts, renames count as added files, paths are never quoted away."""

    def test_an_added_line_starting_with_plus_plus_is_still_read(self) -> None:
        diff = ("diff --git a/x.md b/x.md\n--- a/x.md\n+++ b/x.md\n@@ -0,0 +1,3 @@\n"
                "+++ looks like a header\n+agent " + REAL_PP + "\n+tail\n")
        assert [t for _, t in lint.parse_added(diff)] == ["++ looks like a header", f"agent {REAL_PP}", "tail"]
        assert lint.line_problems("x.md", lint.parse_added(diff)[1][1], DENY)

    def test_line_numbers_follow_the_hunk_header(self) -> None:
        diff = "@@ -5,2 +5,1 @@\n-old one\n-old two\n+new five\n@@ -9 +8,2 @@\n-x\n+eight\n+nine\n"
        assert lint.parse_added(diff) == [(5, "new five"), (8, "eight"), (9, "nine")]

    def test_a_rename_into_a_soul_folder_fails(self, tmp_path, monkeypatch) -> None:
        import subprocess
        g = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@example.invalid")
        g("config", "user.name", "t")
        (tmp_path / "notes.md").write_text("plain notes\n", encoding="utf-8")
        g("add", ".")
        g("commit", "-qm", "base")
        g("checkout", "-qb", "pr")
        (tmp_path / "souls").mkdir()
        g("mv", "notes.md", "souls/zed.md")
        g("commit", "-qm", "rename")
        monkeypatch.chdir(tmp_path)
        assert "souls/zed.md" in lint.changed_files("main", added_only=True)
        assert lint.main("main") == 1

    def test_a_lowercase_real_passport_still_fails(self) -> None:
        assert lint.line_problems("docs/a.md", "agent " + REAL_PP.lower(), DENY)
