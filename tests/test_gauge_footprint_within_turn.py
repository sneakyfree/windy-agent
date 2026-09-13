"""The gas tank must not drain N× on an N-tool turn.

Observed 2026-09-13 on Windy 0 over the bridge: five substantive turns on
claude-opus-5 (200k window) read ``🟢 66% → 🟡 49% → 🔴 0% → 🔴 0%``. The
heaviest turn was a web-search-then-write with several tool rounds.

``_record_session_footprint`` correctly keeps a running MAX across turns —
its docstring explains why summing was "a grandma-killer" (each turn's
``input_tokens`` already contains the whole history). But INSIDE a turn the
tool loop does ``input_tokens += result["input_tokens"]`` on every round,
and that sum is what gets recorded. Each round's ``input_tokens`` already
contains the full prompt too, so a 3-round turn counts the prompt 3×. The
same argument that motivated MAX across turns applies within one.

The docstring also says why this is not cosmetic: the previous turn's
header is part of history, so the model reads "🔴 0%" and emulates being
out of context — terse, "I can't", stops. That is the symptom Grant
described.

This test drives one turn with three LLM rounds whose prompts are ~60k
tokens each and asserts the recorded fill is ~one prompt, not three.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import windyfly.agent.context_header as _ch
import windyfly.agent.loop as _loop
from windyfly.agent.loop import agent_respond
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _clean_session_state(monkeypatch, tmp_path):
    _loop._session_tokens.clear()
    _loop._session_interaction_counts.clear()
    _ch._tracker = None
    monkeypatch.setenv("WINDY_PAUSE_FLAG", str(tmp_path / ".paused"))
    monkeypatch.setenv("WINDY_YOLO_FLAG", str(tmp_path / ".yolo"))
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(tmp_path / ".guest"))
    yield
    _loop._session_tokens.clear()
    _loop._session_interaction_counts.clear()


def _config() -> dict:
    return {
        "agent": {
            "default_model": "gpt-4o-mini",
            "max_context_tokens": 200_000,
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
            "humor_level": 5, "formality": 5, "proactivity": 5,
            "verbosity": 5, "reasoning_depth": 5, "autonomy": 5,
            "epistemic_strictness": 5,
        },
        "costs": {"daily_budget_usd": 100.0, "warn_at_usd": 90.0},
    }


def _round(input_tokens: int, tool_calls):
    return {
        "content": "" if tool_calls else "done",
        "model": "gpt-4o-mini",
        "input_tokens": input_tokens,
        "output_tokens": 100,
        "tool_calls": tool_calls,
    }


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_three_tool_rounds_record_one_prompt_not_three(mock_llm, _online):
    registry = ToolRegistry()
    registry.register(
        "probe", "a tool", {"type": "object", "properties": {}}, lambda: "ok",
    )
    call = [{"id": "c1", "function": {"name": "probe", "arguments": {}}}]
    # Prompts grow a little each round, as they do in real life (tool
    # results get appended). Three rounds, ~60k each.
    mock_llm.side_effect = [
        _round(60_000, call),
        _round(61_000, call),
        _round(62_000, None),
    ]

    db = Database(":memory:")
    wq = WriteQueue()
    wq.start()
    try:
        agent_respond(_config(), db, wq, "do the thing", "gauge-session", registry)
    finally:
        wq.stop()
        db.close()

    assert mock_llm.call_count == 3
    recorded = _loop._session_tokens["gauge-session"]

    # The window actually held ~62k + 300 output at its fullest. Recording
    # 183k here is the bug: it would read 🔴 on a 200k model after a single
    # three-tool turn.
    assert recorded <= 62_000 + 3 * 100 + 1_000, (
        f"recorded fill {recorded:,} for a turn whose largest prompt was 62,000 "
        f"tokens — the tool loop is summing per-round input_tokens (each of "
        f"which already contains the whole prompt) instead of taking the peak"
    )


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_single_round_turn_unchanged(mock_llm, _online):
    """No tool rounds → footprint is simply input + output, as before."""
    mock_llm.side_effect = [_round(5_000, None)]
    db = Database(":memory:")
    wq = WriteQueue()
    wq.start()
    try:
        agent_respond(_config(), db, wq, "hi", "gauge-session-2", None)
    finally:
        wq.stop()
        db.close()
    assert _loop._session_tokens["gauge-session-2"] == 5_000 + 100
