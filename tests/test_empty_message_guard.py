"""Empty-message guard regression test.

Caught by the windy-0 stress harness 2026-04-26 (stress_v1.py
edge_empty case): an empty / whitespace-only ``user_message`` reached
``call_llm`` and Anthropic returned

    Error code: 400 - {'message': 'messages.0: user messages must have
    non-empty content'}

The provider failover ate the cost of one wasted call AND surfaced as a
generic "Sorry, something went wrong" to the user. The fix
short-circuits in ``agent_respond`` before any LLM is consulted.

These tests run WITHOUT mocking the LLM — the guard must return BEFORE
any provider lookup happens, so a missing API key or unreachable
network would be a sign the guard didn't fire (and the test would fail
loudly).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from windyfly.agent.loop import agent_respond
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry


@pytest.fixture
def stack():
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "guard.db"))
        wq = WriteQueue()
        wq.start()
        try:
            yield db, wq, ToolRegistry()
        finally:
            wq.stop()
            db.close()


@pytest.fixture
def config():
    # No real API key required — the guard MUST return before any LLM
    # call. If a test reaches the LLM layer, that's the bug we're
    # trying to prevent.
    return {
        "agent": {
            "default_model": "claude-sonnet-4-6",
            "active_provider": "anthropic",
        },
        "memory": {},
        "personality": {"preset": "buddy"},
    }


@pytest.mark.parametrize("empty_input", ["", " ", "   ", "\t", "\n", " \t \n  ", "\r\n"])
def test_empty_or_whitespace_only_message_short_circuits(stack, config, empty_input):
    """Any empty / whitespace-only message must return a polite prompt
    WITHOUT calling the LLM. The fact that we use no API key proves the
    short-circuit fired."""
    db, wq, tools = stack
    response = agent_respond(config, db, wq, empty_input, "test-empty", tools)
    assert response, f"empty input {empty_input!r} produced empty response"
    low = response.lower()
    assert ("send" in low or "didn't catch" in low or "message" in low), (
        f"guard message changed unexpectedly: {response[:200]!r}"
    )


def test_none_message_does_not_crash(stack, config):
    """Defense-in-depth: if a buggy caller passes ``None`` instead of an
    empty string, we still degrade gracefully (the ``or ""`` in the
    guard makes ``None`` indistinguishable from empty)."""
    db, wq, tools = stack
    response = agent_respond(config, db, wq, None, "test-none", tools)  # type: ignore[arg-type]
    assert response
