"""/goal Phase 3 — autorun (bounded autonomous loop) tests.

Covers:
  - Migration 10 applied (schema_version >= 10; autorun columns)
  - parse_goal_command for /goal autorun [N|stop|garbage]
  - start_autorun clamps to AUTORUN_MAX_TURNS_HARD_CAP
  - decrement_autorun decrements + accumulates tokens
  - stop_autorun zeros remaining
  - run_autorun: happy path runs N turns, emits summary
  - run_autorun: stops on goal status change
  - run_autorun: stops on token cap exceed
  - run_autorun: stops on MET-COMPLETION sentinel
  - run_autorun: stops on cancellation (asyncio.CancelledError)
  - cancel_autorun_for_session cancels the registered task
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from windyfly.agent import goal_autorun
from windyfly.channels.slash_commands import parse_goal_command
from windyfly.memory import goals as goals_mod
from windyfly.memory.database import Database


# ── Migration ────────────────────────────────────────────────────


def test_migration_10_applied():
    db = Database(":memory:")
    ver = db.fetchone("SELECT MAX(version) AS v FROM schema_version")
    assert ver and ver["v"] >= 10
    cols = {r["name"] for r in db.fetchall("PRAGMA table_info(goals)")}
    assert {"autorun_remaining", "autorun_max_turns",
            "autorun_started_at", "autorun_tokens_used"}.issubset(cols)


# ── Parser ───────────────────────────────────────────────────────


class TestAutorunParser:

    def test_bare_defaults_to_5(self):
        assert parse_goal_command("/goal autorun") == (True, "autorun_start", "5")

    def test_numeric_n(self):
        assert parse_goal_command("/goal autorun 3") == (True, "autorun_start", "3")
        assert parse_goal_command("/goal autorun 20") == (True, "autorun_start", "20")

    def test_stop(self):
        assert parse_goal_command("/goal autorun stop") == (True, "autorun_stop", None)
        assert parse_goal_command("/goal autorun cancel") == (True, "autorun_stop", None)
        assert parse_goal_command("/goal autorun off") == (True, "autorun_stop", None)

    def test_invalid(self):
        assert parse_goal_command("/goal autorun garbage")[1] == "autorun_invalid"
        assert parse_goal_command("/goal autorun 0")[1] == "autorun_invalid"
        assert parse_goal_command("/goal autorun -3")[1] == "autorun_invalid"


# ── CRUD ─────────────────────────────────────────────────────────


@pytest.fixture
def db_with_goal():
    db = Database(":memory:")
    gid = goals_mod.create_goal(db, session_id="s1", text="g")
    return db, gid


def test_start_autorun_clamps_to_hard_cap(db_with_goal):
    db, gid = db_with_goal
    capped = goals_mod.start_autorun(db, gid, max_turns=100, chat_id="c")
    assert capped == goals_mod.AUTORUN_MAX_TURNS_HARD_CAP
    g = goals_mod.get_goal(db, gid)
    assert g["autorun_remaining"] == capped
    assert g["autorun_max_turns"] == capped


def test_start_autorun_rejects_zero(db_with_goal):
    db, gid = db_with_goal
    with pytest.raises(ValueError):
        goals_mod.start_autorun(db, gid, max_turns=0)


def test_decrement_autorun_returns_remaining(db_with_goal):
    db, gid = db_with_goal
    goals_mod.start_autorun(db, gid, max_turns=3, chat_id="c")
    assert goals_mod.decrement_autorun(db, gid, tokens_used=100) == 2
    assert goals_mod.decrement_autorun(db, gid, tokens_used=200) == 1
    g = goals_mod.get_goal(db, gid)
    assert g["autorun_tokens_used"] == 300


def test_stop_autorun_zeros_remaining(db_with_goal):
    db, gid = db_with_goal
    goals_mod.start_autorun(db, gid, max_turns=5, chat_id="c")
    goals_mod.stop_autorun(db, gid)
    g = goals_mod.get_goal(db, gid)
    assert g["autorun_remaining"] == 0


# ── Orchestrator ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_autorun_happy_path(db_with_goal):
    """3 turns requested → 3 turns run → summary delivered."""
    db, gid = db_with_goal
    goals_mod.start_autorun(db, gid, max_turns=3, chat_id="chat-x")
    deliver_mock = AsyncMock()

    turn_count = {"n": 0}
    def fake_agent_respond(**kwargs):
        turn_count["n"] += 1
        return f"Turn {turn_count['n']} did a thing."

    out = await goal_autorun.run_autorun(
        goal_id=gid, db=db,
        deliver=deliver_mock, agent_respond=fake_agent_respond,
        config={}, write_queue=MagicMock(),
    )
    assert out["status"] == "ok"
    assert out["turns_run"] == 3
    deliver_mock.assert_called_once()
    args, _ = deliver_mock.call_args
    assert args[0] == "chat-x"
    assert "Autorun complete" in args[1]
    g = goals_mod.get_goal(db, gid)
    assert g["autorun_remaining"] == 0


@pytest.mark.asyncio
async def test_run_autorun_stops_on_met_completion(db_with_goal):
    """Worker emitting MET-COMPLETION ends the run early."""
    db, gid = db_with_goal
    goals_mod.start_autorun(db, gid, max_turns=10, chat_id="chat-x")
    deliver_mock = AsyncMock()

    turn_count = {"n": 0}
    def fake_agent_respond(**kwargs):
        turn_count["n"] += 1
        if turn_count["n"] == 2:
            return "All done now.\nMET-COMPLETION"
        return f"Turn {turn_count['n']}."

    out = await goal_autorun.run_autorun(
        goal_id=gid, db=db, deliver=deliver_mock,
        agent_respond=fake_agent_respond, config={},
        write_queue=MagicMock(),
    )
    assert out["turns_run"] == 2
    assert "MET-COMPLETION" in out["abort_reason"]


@pytest.mark.asyncio
async def test_run_autorun_stops_on_goal_completed(db_with_goal):
    """If the goal is marked completed externally mid-run, the next
    iteration aborts."""
    db, gid = db_with_goal
    goals_mod.start_autorun(db, gid, max_turns=10, chat_id="chat-x")
    deliver_mock = AsyncMock()

    turn_count = {"n": 0}
    def fake_agent_respond(**kwargs):
        turn_count["n"] += 1
        if turn_count["n"] == 1:
            # Simulate evaluator-driven goal completion mid-run
            goals_mod.complete_goal(db, gid, closing_note="evaluator says met")
        return f"Turn {turn_count['n']}."

    out = await goal_autorun.run_autorun(
        goal_id=gid, db=db, deliver=deliver_mock,
        agent_respond=fake_agent_respond, config={},
        write_queue=MagicMock(),
    )
    # 1 turn ran, then the check at top of iter 2 sees completed
    assert out["turns_run"] == 1
    assert "completed" in out["abort_reason"]


@pytest.mark.asyncio
async def test_run_autorun_stops_on_token_cap(db_with_goal, monkeypatch):
    """When autorun_tokens_used exceeds AUTORUN_MAX_TOKENS_PER_RUN
    the loop aborts."""
    db, gid = db_with_goal
    # Drop the cap to something small so a single turn trips it.
    monkeypatch.setattr(goals_mod, "AUTORUN_MAX_TOKENS_PER_RUN", 10)
    goals_mod.start_autorun(db, gid, max_turns=10, chat_id="chat-x")
    deliver_mock = AsyncMock()

    def fake_agent_respond(**kwargs):
        return "A reply long enough to bump the token estimate past 10 chars / 4 = several tokens."

    out = await goal_autorun.run_autorun(
        goal_id=gid, db=db, deliver=deliver_mock,
        agent_respond=fake_agent_respond, config={},
        write_queue=MagicMock(),
    )
    assert "token cap" in out["abort_reason"]


@pytest.mark.asyncio
async def test_run_autorun_no_chat_id_short_circuits(db_with_goal):
    """If chat_id never got set, autorun should refuse rather than
    silently producing replies it can't deliver."""
    db, gid = db_with_goal
    # Start autorun WITHOUT providing chat_id
    goals_mod.start_autorun(db, gid, max_turns=3, chat_id=None)
    # Manually unset chat_id to simulate a row created without it
    db.execute("UPDATE goals SET chat_id = NULL WHERE id = ?", (gid,))
    db.commit()

    out = await goal_autorun.run_autorun(
        goal_id=gid, db=db, deliver=AsyncMock(),
        agent_respond=lambda **k: "x", config={},
        write_queue=MagicMock(),
    )
    assert out["status"] == "no_chat_id"


@pytest.mark.asyncio
async def test_cancel_autorun_for_session(db_with_goal):
    """The cancel hook the channel layer calls on incoming user
    messages should stop a running autorun for the session."""
    db, gid = db_with_goal
    goals_mod.start_autorun(db, gid, max_turns=5, chat_id="chat-x")

    # Fake a long-running autorun task in the registry
    stop_evt = asyncio.Event()

    async def long_running():
        try:
            await stop_evt.wait()
        except asyncio.CancelledError:
            stop_evt.set()
            raise

    task = asyncio.create_task(long_running())
    goal_autorun.register_autorun_task(gid, task)

    cancelled = goal_autorun.cancel_autorun_for_session(db, "s1")
    assert cancelled is True

    # Wait briefly for cancellation to propagate
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass

    # And the DB flag is cleared
    g = goals_mod.get_goal(db, gid)
    assert g["autorun_remaining"] == 0


@pytest.mark.asyncio
async def test_cancel_autorun_returns_false_when_nothing_running():
    db = Database(":memory:")
    goals_mod.create_goal(db, session_id="s1", text="g")
    assert goal_autorun.cancel_autorun_for_session(db, "s1") is False
