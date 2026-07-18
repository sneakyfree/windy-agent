"""/goal UX polish bundle — shell.exec discoverability, web_search
gating UX, per-reply 🎯 visibility footer.

These three small fixes were deferred from the /goal Phase 1 sprint
to keep that PR lean. Shipping together as a single coherent
"grandma UX polish" PR.
"""

from __future__ import annotations

import pytest

# ── shell.exec discoverability (v15 finding #3) ──────────────────


# TestShellExecDiscoverability RETIRED 2026-07-18: the keyword-trigger
# helpers it pinned were deleted in the steering->substrate migration
# (shell.exec's own description now carries the OS-state guidance; the
# origin failure "bot won't shell for disk usage" moves to the weekly
# continuity battery as a live-model case).


def test_web_search_unavailable_returns_grandma_friendly():
    """When windy-search is gated off, the error must be
    grandma-readable and tell the model what to do."""
    import os
    from unittest.mock import patch
    with patch.dict(os.environ, {}, clear=True):
        from windyfly.tools import web_search as ws
        with pytest.raises(RuntimeError) as exc_info:
            ws.web_search("anything")
    msg = str(exc_info.value)
    assert "WEB_SEARCH_UNAVAILABLE" in msg
    assert "not connected to the web" in msg
    assert "Do NOT retry" in msg  # tells the agent not to spin


# ── Per-reply 🎯 visibility footer (Phase 1 deferred) ────────────


@pytest.fixture
def stack():
    from windyfly.memory.database import Database
    from windyfly.memory.episodes import save_episode
    from windyfly.memory.write_queue import WriteQueue
    db = Database(":memory:")
    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    wq = WriteQueue()
    wq.start()
    yield {
        "agent": {"default_model": "claude-haiku-4-5-20251001",
                  "max_context_tokens": 8000, "max_response_tokens": 1024,
                  "temperature": 0.5},
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 20,
                   "max_nodes_per_context": 10},
        "personality": {"soul_path": "SOUL.md", "humor_level": 5,
                        "formality": 5, "proactivity": 5, "verbosity": 5,
                        "reasoning_depth": 5, "autonomy": 5,
                        "epistemic_strictness": 5},
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }, db, wq
    try:
        wq.stop()
    except Exception:
        pass
    db.close()


def test_visibility_footer_appears_with_active_goal(stack):
    """When a goal is active and a reply is generated, the bot's
    reply should end with the 🎯 still on footer."""
    from unittest.mock import patch
    config, db, wq = stack
    from windyfly.memory.goals import create_goal
    create_goal(db, session_id="vis-1", text="Plan my Yellowstone trip")

    from windyfly.agent.loop import agent_respond
    with patch("windyfly.agent.loop.is_online", return_value=True), \
         patch("windyfly.agent.loop.call_llm") as mock_llm, \
         patch("windyfly.agent.goal_evaluator.call_llm") as mock_eval:
        mock_llm.return_value = {
            "content": "Yellowstone trip plan in progress.",
            "input_tokens": 10, "output_tokens": 5,
            "cost": 0.0, "tool_calls": None,
        }
        mock_eval.return_value = {
            "content": '{"verdict": "advanced", "reason": "step taken", "progress_note": null}',
            "input_tokens": 5, "output_tokens": 5,
        }
        reply = agent_respond(config, db, wq, "what's next?", "vis-1")

    assert "🎯 still on" in reply
    assert "Yellowstone" in reply


def test_visibility_footer_skipped_when_goal_just_completed(stack):
    """If the evaluator marks goal MET on this turn, the completion
    message takes over — no duplicate 🎯 still on footer."""
    from unittest.mock import patch
    config, db, wq = stack
    from windyfly.memory.goals import create_goal
    create_goal(db, session_id="vis-2", text="Quick goal")

    from windyfly.agent.loop import agent_respond
    with patch("windyfly.agent.loop.is_online", return_value=True), \
         patch("windyfly.agent.loop.call_llm") as mock_llm, \
         patch("windyfly.agent.goal_evaluator.call_llm") as mock_eval:
        mock_llm.return_value = {
            "content": "Done!", "input_tokens": 5, "output_tokens": 2,
            "cost": 0.0, "tool_calls": None,
        }
        mock_eval.return_value = {
            "content": '{"verdict": "met", "reason": "user said thanks", "progress_note": null}',
            "input_tokens": 5, "output_tokens": 5,
        }
        reply = agent_respond(config, db, wq, "thanks!", "vis-2")

    assert "Goal achieved" in reply
    # Footer should NOT also appear — would be redundant
    assert "🎯 still on" not in reply


def test_visibility_footer_absent_when_no_goal(stack):
    """No active goal → no footer."""
    from unittest.mock import patch
    config, db, wq = stack
    from windyfly.agent.loop import agent_respond
    with patch("windyfly.agent.loop.is_online", return_value=True), \
         patch("windyfly.agent.loop.call_llm") as mock_llm:
        mock_llm.return_value = {
            "content": "Hello!", "input_tokens": 5, "output_tokens": 2,
            "cost": 0.0, "tool_calls": None,
        }
        reply = agent_respond(config, db, wq, "hi", "no-goal-1")
    assert "🎯 still on" not in reply
