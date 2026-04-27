"""Empty-after-tool-loop regression test.

Caught by stress harness v2 G_naming case 2026-04-26: prompting
"Brainstorm 5 names for an open-source AI agent..." caused the LLM
to call ``shape_shift(preset=writer)`` then ``shape_shift_restore``
and never produce text content. The tool loop exited with
``response_text=""`` and the user got 42 chars of context-header +
nothing else — looked like the bot was crashed.

Defense lives in ``agent_respond`` (loop.py) immediately after the
analytics block and before episode-save. If the response_text is
empty/whitespace at that point, substitute a fallback message so
the user always gets *something*.

This test exercises the defense with a mocked LLM that returns
tool_calls but no text content — the same pattern that produced
the production failure.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from windyfly.agent.loop import agent_respond
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry


@pytest.fixture
def stack():
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "fallback.db"))
        wq = WriteQueue()
        wq.start()
        try:
            yield db, wq, ToolRegistry()
        finally:
            wq.stop()
            db.close()


@pytest.fixture
def config():
    return {
        "agent": {
            "default_model": "claude-sonnet-4-6",
            "active_provider": "anthropic",
        },
        "memory": {},
        "personality": {"preset": "buddy"},
    }


def _empty_text_with_tool_calls(*args, **kwargs):
    """Mock call_llm that returns 0-char content + a tool call.

    Mirrors the shape_shift→restore pattern from the production
    failure: LLM keeps calling tools, never writes assistant text.
    On the first call returns a tool_call; on subsequent calls
    returns empty content with no further tool_calls (loop exits
    without text).
    """
    if not hasattr(_empty_text_with_tool_calls, "called"):
        _empty_text_with_tool_calls.called = 0
    _empty_text_with_tool_calls.called += 1
    if _empty_text_with_tool_calls.called == 1:
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "shape_shift", "arguments": '{"preset": "writer"}'},
                }
            ],
            "input_tokens": 100,
            "output_tokens": 5,
        }
    # Subsequent calls: empty content, no tool calls → exits loop empty
    return {
        "content": "",
        "tool_calls": None,
        "input_tokens": 50,
        "output_tokens": 0,
    }


def test_empty_response_after_tool_loop_returns_fallback_not_silence(
    stack, config, monkeypatch
) -> None:
    """The contract: agent_respond must NEVER return a string whose
    .strip() is empty. The user always gets words back."""
    db, wq, tools = stack
    # Reset the mock counter for a clean test
    if hasattr(_empty_text_with_tool_calls, "called"):
        del _empty_text_with_tool_calls.called
    # Patch call_llm so no real network call happens
    with patch("windyfly.agent.loop.call_llm", side_effect=_empty_text_with_tool_calls):
        # Stub out tool execution so it doesn't fail
        with patch("windyfly.agent.loop._dispatch_tool_call",
                   return_value='{"ok": true}'):
            response = agent_respond(
                config, db, wq, "brainstorm 5 names please",
                "test-empty-tools", tools,
            )
    # Contract: response is non-empty even if you strip the header.
    assert response, "agent returned None"
    # The context-header prefix is short (~50 chars). After stripping
    # it, there must still be content — that's the bug we're guarding.
    body = response
    if "]" in response:
        # Header has form "[🪰 Windy Fly · ...]\n\n" — strip past the ]\n\n
        idx = response.find("]")
        body = response[idx+1:].strip()
    assert body, (
        f"agent returned only the context-header prefix with no body: "
        f"{response!r}"
    )
    # And the fallback should mention "tools" so it's debuggable
    low = body.lower()
    assert any(word in low for word in ("tool", "distracted", "asking again")), (
        f"fallback message should hint at the tool-loop issue: {body!r}"
    )


def test_normal_response_unchanged_by_defense(stack, config) -> None:
    """Defense must NOT trigger when LLM returns real content."""
    db, wq, tools = stack
    real_response = {
        "content": "Hi there! Here are 5 names:\n1. Sage\n2. Atlas\n3. Echo\n4. Vault\n5. Mosaic",
        "tool_calls": None,
        "input_tokens": 100,
        "output_tokens": 50,
    }
    with patch("windyfly.agent.loop.call_llm", return_value=real_response):
        response = agent_respond(
            config, db, wq, "name 5 things", "test-normal", tools,
        )
    assert "Sage" in response
    # Defense fallback message should NOT appear
    assert "distracted by my own tools" not in response.lower()
