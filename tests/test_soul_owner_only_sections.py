"""Owner-only soul sections (Boss BOARD 2026-11-07T14:31:58Z (1)).

A soul heading marked ``<!-- owner-only -->`` keeps that section (and its sub-sections) out of every turn that is not
the owner's. The owner's turns get the whole soul. The marker never reaches a model, and a soul with no marker is
returned byte for byte, so every agent without markers (the default SOUL.md, the fallback) is unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from windyfly.personality.engine import load_soul, soul_for_turn

SOUL = (
    "# Example soul\n"
    "\n"
    "## Who you are\n"
    "A helpful agent.\n"
    "\n"
    "## Private notes <!-- owner-only -->\n"
    "PRIVATE-LINE-1\n"
    "### Sub notes\n"
    "PRIVATE-LINE-2\n"
    "```\n"
    "## not a heading inside a fence\n"
    "PRIVATE-LINE-3\n"
    "```\n"
    "\n"
    "## Values\n"
    "Be honest.\n"
)


class TestSoulForTurn:
    def test_non_owner_loses_the_marked_section_and_its_sub_sections(self) -> None:
        out = soul_for_turn(SOUL, owner=False)
        assert "PRIVATE-LINE" not in out
        assert "Private notes" not in out and "Sub notes" not in out
        assert "## Who you are\nA helpful agent." in out
        assert "## Values\nBe honest." in out          # the next sibling section comes back

    def test_owner_keeps_everything_without_the_marker(self) -> None:
        out = soul_for_turn(SOUL, owner=True)
        for line in ("PRIVATE-LINE-1", "PRIVATE-LINE-2", "PRIVATE-LINE-3", "## Private notes\n", "### Sub notes"):
            assert line in out
        assert "owner-only" not in out
        assert out == SOUL.replace(" <!-- owner-only -->", "")

    @pytest.mark.parametrize("owner", [True, False])
    def test_a_soul_without_markers_is_unchanged_byte_for_byte(self, owner: bool) -> None:
        plain = SOUL.replace(" <!-- owner-only -->", "")
        assert soul_for_turn(plain, owner=owner) == plain

    @pytest.mark.parametrize("owner", [True, False])
    def test_the_default_soul_and_the_fallback_are_unchanged(self, owner: bool) -> None:
        default = (Path(__file__).resolve().parents[1] / "SOUL.md").read_text(encoding="utf-8")
        assert soul_for_turn(default, owner=owner) == default
        fallback = load_soul("/nonexistent/SOUL.md")
        assert soul_for_turn(fallback, owner=owner) == fallback

    @pytest.mark.parametrize("mark", ["<!-- owner-only -->", "<!--owner-only-->", "<!--  OWNER-ONLY  -->"])
    def test_marker_spelling_variants(self, mark: str) -> None:
        soul = f"## Keep\nkeep\n## Hide {mark}\nSECRET\n## Also keep\nok\n"
        assert "SECRET" not in soul_for_turn(soul, owner=False)
        owner_out = soul_for_turn(soul, owner=True)
        assert "SECRET" in owner_out and "owner-only" not in owner_out.lower()

    def test_a_marked_sub_section_hides_only_itself(self) -> None:
        soul = "## Outer\nouter text\n### Inner <!-- owner-only -->\nINNER-SECRET\n### Sibling\nsibling text\n## Next\nnext\n"
        out = soul_for_turn(soul, owner=False)
        assert "INNER-SECRET" not in out
        assert "outer text" in out and "sibling text" in out and "next" in out

    def test_a_marker_inside_a_code_fence_is_not_a_heading(self) -> None:
        soul = "## A\n```\n## Fenced <!-- owner-only -->\nFENCED\n```\n## B\nb\n"
        assert soul_for_turn(soul, owner=False) == soul


class TestPromptUsesTheGate:
    """End to end: assemble_prompt gives a stranger's turn the soul without the marked section."""

    @staticmethod
    def _config(soul_path: str) -> dict:
        return {
            "agent": {"default_model": "claude-haiku-4-5-20251001", "max_context_tokens": 8000,
                      "max_response_tokens": 2000, "temperature": 0.7},
            "memory": {"db_path": ":memory:", "max_episodes_per_context": 20, "max_nodes_per_context": 10},
            "personality": {"soul_path": soul_path, "humor_level": 7, "formality": 4, "proactivity": 5,
                            "verbosity": 5, "reasoning_depth": 6, "autonomy": 3, "epistemic_strictness": 5},
            "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
        }

    @pytest.fixture
    def db(self):
        from windyfly.memory.database import Database
        from windyfly.memory.episodes import save_episode
        db = Database(":memory:")
        save_episode(db, "user", "bootstrap", session_id="bootstrap")
        yield db
        db.close()

    @staticmethod
    def _system_text(msgs: list[dict]) -> str:
        return "\n".join(str(m.get("content", "")) for m in msgs if m.get("role") == "system")

    def test_stranger_turn_never_sees_the_marked_section(self, db, tmp_path: Path) -> None:
        from windyfly.agent.prompt import assemble_prompt
        soul = tmp_path / "SOUL.md"
        soul.write_text(SOUL, encoding="utf-8")
        text = self._system_text(assemble_prompt(self._config(str(soul)), db, "hi", "s-stranger", band=1))
        assert "PRIVATE-LINE" not in text and "owner-only" not in text
        assert "A helpful agent." in text and "Be honest." in text

    def test_owner_turn_sees_the_whole_soul(self, db, tmp_path: Path) -> None:
        from windyfly.agent.prompt import assemble_prompt
        soul = tmp_path / "SOUL.md"
        soul.write_text(SOUL, encoding="utf-8")
        text = self._system_text(assemble_prompt(self._config(str(soul)), db, "hi", "s-owner", band=None))
        assert "PRIVATE-LINE-1" in text and "PRIVATE-LINE-2" in text
        assert "owner-only" not in text
