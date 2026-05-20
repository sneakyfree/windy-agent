"""/goal slash command — full-stack contract tests.

Covers:
  - Goals table migration applied (schema_version >= 8)
  - memory/goals.py CRUD: create/get/list/abandon/complete/expire
  - One-active-goal-per-session invariant (auto-abandon on replace)
  - Consecutive-unrelated counter reset on any non-unrelated verdict
  - Evaluator-history JSON pruning
  - slash_commands.parse_goal_command for set/status/clear/done/aliases
  - goal_evaluator JSON parsing (raw, fenced, sloppy-with-prose)
  - goal_evaluator falls back to BLOCKED on LLM failure (loop must not crash)
  - assemble_prompt injects 🎯 ACTIVE GOAL block when goal exists
  - assemble_prompt omits goal block when no active goal
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from windyfly.agent.goal_evaluator import (
    _parse_evaluator_json,
    evaluate_goal,
)
from windyfly.channels.slash_commands import parse_goal_command
from windyfly.memory import goals as goals_mod
from windyfly.memory.database import Database


# ── Migration ────────────────────────────────────────────────────


def test_goals_table_created_on_fresh_db():
    db = Database(":memory:")
    row = db.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='goals'"
    )
    assert row is not None
    # Migration version 8 should be recorded
    ver = db.fetchone(
        "SELECT MAX(version) AS v FROM schema_version"
    )
    assert ver and ver["v"] >= 8


# ── CRUD ─────────────────────────────────────────────────────────


@pytest.fixture
def db():
    return Database(":memory:")


def test_create_and_get_active_goal(db):
    gid = goals_mod.create_goal(
        db, session_id="s1", text="Plan Yellowstone trip",
    )
    active = goals_mod.get_active_goal(db, "s1")
    assert active is not None
    assert active["id"] == gid
    assert active["text"] == "Plan Yellowstone trip"
    assert active["status"] == "active"
    assert active["turns_count"] == 0


def test_one_active_goal_per_session(db):
    """Setting a new goal while one is active must abandon the old
    one — exactly one active goal per session is the invariant."""
    g1 = goals_mod.create_goal(db, session_id="s1", text="goal one")
    g2 = goals_mod.create_goal(db, session_id="s1", text="goal two")
    assert g1 != g2

    active = goals_mod.get_active_goal(db, "s1")
    assert active["id"] == g2

    old = goals_mod.get_goal(db, g1)
    assert old["status"] == "abandoned"
    assert "replaced" in (old["closing_note"] or "")


def test_different_sessions_can_both_have_active_goals(db):
    g1 = goals_mod.create_goal(db, session_id="sA", text="A")
    g2 = goals_mod.create_goal(db, session_id="sB", text="B")
    a = goals_mod.get_active_goal(db, "sA")
    b = goals_mod.get_active_goal(db, "sB")
    assert a["id"] == g1
    assert b["id"] == g2


def test_empty_goal_text_rejected(db):
    with pytest.raises(ValueError):
        goals_mod.create_goal(db, session_id="s1", text="   ")


def test_long_goal_text_truncated(db):
    long = "x" * 1500
    gid = goals_mod.create_goal(db, session_id="s1", text=long)
    g = goals_mod.get_goal(db, gid)
    assert len(g["text"]) <= 800
    assert g["text"].endswith("...")


def test_record_turn_increments_counters(db):
    gid = goals_mod.create_goal(db, session_id="s1", text="g")
    goals_mod.record_turn(db, gid, tokens_input=100, tokens_output=50)
    goals_mod.record_turn(db, gid, tokens_input=200, tokens_output=80)
    g = goals_mod.get_goal(db, gid)
    assert g["turns_count"] == 2
    assert g["tokens_input"] == 300
    assert g["tokens_output"] == 130


def test_record_evaluation_appends_history_and_tracks_unrelated(db):
    gid = goals_mod.create_goal(db, session_id="s1", text="g")
    goals_mod.record_evaluation(
        db, gid, verdict=goals_mod.VERDICT_ADVANCED, reason="made progress",
        progress_note="found 3 flights",
    )
    g = goals_mod.get_goal(db, gid)
    history = json.loads(g["evaluator_history"])
    assert len(history) == 1
    assert history[0]["verdict"] == "advanced"
    assert history[0]["progress_note"] == "found 3 flights"
    assert g["consecutive_unrelated"] == 0


def test_consecutive_unrelated_resets_on_other_verdict(db):
    gid = goals_mod.create_goal(db, session_id="s1", text="g")
    goals_mod.record_evaluation(db, gid, verdict=goals_mod.VERDICT_UNRELATED, reason="off-topic")
    goals_mod.record_evaluation(db, gid, verdict=goals_mod.VERDICT_UNRELATED, reason="off-topic")
    g = goals_mod.get_goal(db, gid)
    assert g["consecutive_unrelated"] == 2
    goals_mod.record_evaluation(db, gid, verdict=goals_mod.VERDICT_ADVANCED, reason="back on track")
    g = goals_mod.get_goal(db, gid)
    assert g["consecutive_unrelated"] == 0


def test_evaluator_history_bounded(db):
    gid = goals_mod.create_goal(db, session_id="s1", text="g")
    for i in range(goals_mod.MAX_EVAL_HISTORY + 20):
        goals_mod.record_evaluation(
            db, gid, verdict=goals_mod.VERDICT_ADVANCED, reason=f"t{i}",
        )
    g = goals_mod.get_goal(db, gid)
    history = json.loads(g["evaluator_history"])
    assert len(history) == goals_mod.MAX_EVAL_HISTORY


def test_invalid_verdict_raises(db):
    gid = goals_mod.create_goal(db, session_id="s1", text="g")
    with pytest.raises(ValueError):
        goals_mod.record_evaluation(db, gid, verdict="maybe", reason="?")


def test_complete_abandon_expire_paths(db):
    g_a = goals_mod.create_goal(db, session_id="sa", text="A")
    g_b = goals_mod.create_goal(db, session_id="sb", text="B")
    g_c = goals_mod.create_goal(db, session_id="sc", text="C")
    goals_mod.complete_goal(db, g_a, closing_note="done")
    goals_mod.abandon_goal(db, g_b, closing_note="user cleared")
    goals_mod.expire_goal(db, g_c)

    assert goals_mod.get_goal(db, g_a)["status"] == "completed"
    assert goals_mod.get_goal(db, g_b)["status"] == "abandoned"
    assert goals_mod.get_goal(db, g_c)["status"] == "expired"
    # And none should show as active any more
    for s in ("sa", "sb", "sc"):
        assert goals_mod.get_active_goal(db, s) is None


def test_progress_notes_returns_recent_first(db):
    gid = goals_mod.create_goal(db, session_id="s1", text="g")
    goals_mod.record_evaluation(db, gid, verdict=goals_mod.VERDICT_ADVANCED, reason="r1", progress_note="A")
    goals_mod.record_evaluation(db, gid, verdict=goals_mod.VERDICT_BLOCKED, reason="r2")  # no note
    goals_mod.record_evaluation(db, gid, verdict=goals_mod.VERDICT_ADVANCED, reason="r3", progress_note="B")
    g = goals_mod.get_goal(db, gid)
    notes = goals_mod.get_progress_notes(g)
    assert notes == ["B", "A"]


# ── Slash parser ─────────────────────────────────────────────────


class TestGoalParser:

    def test_bare_returns_status(self):
        assert parse_goal_command("/goal") == (True, "status", None)
        assert parse_goal_command("  /GOAL  ") == (True, "status", None)

    def test_aliases_match(self):
        for alias in ("/objective", "/mission"):
            assert parse_goal_command(alias) == (True, "status", None)

    def test_set_with_text(self):
        is_cmd, sub, arg = parse_goal_command("/goal Plan my Yellowstone trip")
        assert is_cmd is True
        assert sub == "set"
        assert arg == "Plan my Yellowstone trip"

    def test_status_subcommand(self):
        assert parse_goal_command("/goal status") == (True, "status", None)
        assert parse_goal_command("/goal show") == (True, "status", None)
        assert parse_goal_command("/goal ?") == (True, "status", None)

    def test_clear_subcommand(self):
        for w in ("clear", "cancel", "abandon", "stop", "reset"):
            assert parse_goal_command(f"/goal {w}") == (True, "clear", None)

    def test_done_subcommand(self):
        for w in ("done", "complete", "finished", "finish"):
            assert parse_goal_command(f"/goal {w}") == (True, "done", None)

    def test_non_command_passes_through(self):
        assert parse_goal_command("hello") == (False, None, None)
        assert parse_goal_command("") == (False, None, None)
        assert parse_goal_command(None) == (False, None, None)
        # bare /resurrect must not collide
        assert parse_goal_command("/resurrect") == (False, None, None)

    def test_goal_containing_status_word_is_still_set(self):
        """'/goal status of my taxes' should be SET, not status-query.
        Multi-word args are always set; only exact single-word
        subcommands trigger status/clear/done."""
        is_cmd, sub, arg = parse_goal_command("/goal status of my taxes")
        assert is_cmd is True
        assert sub == "set"
        assert arg == "status of my taxes"


# ── Evaluator JSON parsing ───────────────────────────────────────


class TestEvaluatorJsonParse:

    def test_raw_json(self):
        out = _parse_evaluator_json('{"verdict": "met", "reason": "done"}')
        assert out == {"verdict": "met", "reason": "done"}

    def test_fenced_json(self):
        text = '```json\n{"verdict": "advanced", "reason": "x"}\n```'
        out = _parse_evaluator_json(text)
        assert out == {"verdict": "advanced", "reason": "x"}

    def test_prose_around_json(self):
        text = 'Here is my verdict:\n{"verdict": "blocked", "reason": "y"}\nthat is all.'
        out = _parse_evaluator_json(text)
        assert out == {"verdict": "blocked", "reason": "y"}

    def test_garbage_returns_none(self):
        assert _parse_evaluator_json("not json at all") is None
        assert _parse_evaluator_json("") is None


class TestEvaluatorIntegration:

    def test_evaluator_falls_back_to_blocked_on_llm_failure(self):
        """An LLM call exception must NOT bubble — the loop relies on
        always getting a verdict dict back."""
        with patch("windyfly.agent.goal_evaluator.call_llm",
                   side_effect=RuntimeError("LLM down")):
            out = evaluate_goal(
                "Plan Yellowstone trip",
                [{"role": "user", "content": "hi"}],
            )
        assert out["verdict"] == "blocked"
        assert "unavailable" in out["reason"].lower()

    def test_evaluator_rejects_unknown_verdict(self):
        """If the evaluator hallucinates a verdict like 'kinda',
        treat it as blocked so the goal isn't mis-completed."""
        fake_resp = {
            "content": '{"verdict": "kinda", "reason": "fuzzy"}',
            "input_tokens": 10, "output_tokens": 5,
        }
        with patch("windyfly.agent.goal_evaluator.call_llm",
                   return_value=fake_resp):
            out = evaluate_goal(
                "g", [{"role": "user", "content": "hi"}],
            )
        assert out["verdict"] == "blocked"

    def test_evaluator_returns_met_when_llm_says_so(self):
        fake_resp = {
            "content": '{"verdict": "met", "reason": "user said thanks"}',
            "input_tokens": 10, "output_tokens": 5,
        }
        with patch("windyfly.agent.goal_evaluator.call_llm",
                   return_value=fake_resp):
            out = evaluate_goal(
                "Plan trip",
                [{"role": "user", "content": "thanks, that's perfect"}],
            )
        assert out["verdict"] == "met"
        assert "thanks" in out["reason"]


# ── Prompt block injection ───────────────────────────────────────


def _make_config():
    return {
        "agent": {"default_model": "claude-haiku-4-5-20251001",
                  "max_context_tokens": 8000, "max_response_tokens": 1024,
                  "temperature": 0.5},
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 5,
                   "max_nodes_per_context": 5},
        "personality": {"soul_path": "SOUL.md", "humor_level": 5,
                        "formality": 5, "proactivity": 5, "verbosity": 5,
                        "reasoning_depth": 5, "autonomy": 5,
                        "epistemic_strictness": 5},
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }


def test_assemble_prompt_includes_goal_block_when_active(db):
    from windyfly.agent.prompt import assemble_prompt
    from windyfly.memory.episodes import save_episode

    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    goals_mod.create_goal(
        db, session_id="test-session",
        text="Plan my Yellowstone trip with kids",
    )
    msgs = assemble_prompt(_make_config(), db, "hi", "test-session")
    sys_text = "\n".join(m["content"] for m in msgs if m.get("role") == "system")

    assert "🎯 ACTIVE GOAL" in sys_text
    assert "Plan my Yellowstone trip with kids" in sys_text
    assert "Don't recap the goal" in sys_text


def test_assemble_prompt_omits_goal_block_when_none_active(db):
    from windyfly.agent.prompt import assemble_prompt
    from windyfly.memory.episodes import save_episode

    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    msgs = assemble_prompt(_make_config(), db, "hi", "test-no-goal")
    sys_text = "\n".join(m["content"] for m in msgs if m.get("role") == "system")
    assert "🎯 ACTIVE GOAL" not in sys_text


def test_assemble_prompt_goal_block_scoped_to_session(db):
    """A goal on session A must not bleed into session B's prompt."""
    from windyfly.agent.prompt import assemble_prompt
    from windyfly.memory.episodes import save_episode

    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    goals_mod.create_goal(db, session_id="sA", text="A-only goal")

    msgs = assemble_prompt(_make_config(), db, "hi", "sB")
    sys_text = "\n".join(m["content"] for m in msgs if m.get("role") == "system")
    assert "A-only goal" not in sys_text
