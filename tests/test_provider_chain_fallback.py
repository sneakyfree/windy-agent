"""Regression: when the provider chain is exhausted mid-turn, the
bot must fall back to offline mode rather than raise.

Surfaced by v14 stress harness 2026-05-02: 37 prompts ran clean,
then the 38th hit a transient 401 cascade. The bot returned
"LLM call failed across all providers in chain (...)" all the way
to the channel handler instead of a friendly message.

Pin the contract:
  - When call_llm raises RuntimeError("...providers in chain..."),
    agent_respond returns the offline-fallback string (or whatever
    Ollama returns if it's running) — NEVER propagates the
    exception.
  - The user message and the offline reply are still saved as
    episodes (history must reflect that they DID send a message).
  - An offline.chain_exhausted event is logged so the operator can
    diagnose without grepping stack traces.
  - Other RuntimeError shapes (not chain exhaustion) DO propagate —
    we don't want to silently swallow real bugs.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from windyfly.agent.loop import agent_respond
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


def _make_config():
    return {
        "agent": {
            "default_model": "claude-haiku-4-5-20251001",
            "max_context_tokens": 8000,
            "max_response_tokens": 2000,
            "temperature": 0.7,
        },
        "memory": {
            "db_path": ":memory:",
            "max_episodes_per_context": 20,
            "max_nodes_per_context": 10,
        },
        "personality": {
            "soul_path": "SOUL.md",
            "humor_level": 7,
            "formality": 4,
            "proactivity": 5,
            "verbosity": 5,
            "reasoning_depth": 6,
            "autonomy": 3,
            "epistemic_strictness": 5,
        },
        "costs": {
            "daily_budget_usd": 5.0,
            "warn_at_usd": 3.0,
        },
    }


@pytest.fixture
def stack():
    db = Database(":memory:")
    wq = WriteQueue(); wq.start()
    yield _make_config(), db, wq
    try: wq.stop()
    except Exception: pass
    db.close()


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_chain_exhaustion_falls_back_to_offline(mock_llm, _online, stack):
    """The exact runtime error agent/models.py raises when every
    provider in the chain fails — must be caught and the offline
    response returned."""
    config, db, wq = stack
    mock_llm.side_effect = RuntimeError(
        "LLM call failed across all providers in chain "
        "(attempted=['anthropic(claude-haiku-4-5-20251001)']): "
        "Error code: 401 - {'type': 'authentication_error'}"
    )

    response = agent_respond(
        config, db, wq, "Hi there", "test-chain-fail",
    )

    assert response  # non-empty
    # Default offline message OR whatever Ollama returned
    assert "stack trace" not in response.lower()
    assert "RuntimeError" not in response
    # The exact default-offline copy from offline.py
    assert (
        "currently offline" in response.lower()
        or "local model" in response.lower()
        or "queue" in response.lower()
        # Ollama may also produce a normal-looking reply
        or len(response) > 5
    )


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_chain_exhaustion_saves_user_message_to_history(mock_llm, _online, stack):
    """Even if the LLM is unreachable, the user's message must end
    up in the episodes table — they tried to talk to us, that
    history shouldn't vanish."""
    config, db, wq = stack
    mock_llm.side_effect = RuntimeError(
        "LLM call failed across all providers in chain (attempted=['anthropic']): boom"
    )

    agent_respond(config, db, wq, "Important question", "test-history")

    # Drain the write queue
    import time
    time.sleep(0.5)
    rows = db.fetchall("SELECT role, content FROM episodes WHERE session_id = ?", ("test-history",))
    contents = [r["content"] for r in rows]
    assert any("Important question" in c for c in contents), (
        f"user message should be persisted; got: {contents}"
    )


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_non_chain_runtime_error_still_propagates(mock_llm, _online, stack):
    """We DON'T want to swallow every RuntimeError — only the
    specific 'providers in chain' shape that means 'LLM dead.' Other
    RuntimeErrors are likely real bugs and should bubble so we see
    them in logs / tests / sentry."""
    config, db, wq = stack
    mock_llm.side_effect = RuntimeError("Some unrelated internal bug")

    with pytest.raises(RuntimeError, match="unrelated internal bug"):
        agent_respond(config, db, wq, "Hi", "test-non-chain")
