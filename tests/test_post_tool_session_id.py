"""Every model call in a turn carries the turn's session_id, including the one after a tool round.

The post-tool call (and the confabulation retries) dropped it, so their cost-ledger rows had no
session and a direct-Anthropic install could miss its long-context beta header on those rounds.
"""

from __future__ import annotations

from unittest.mock import patch

from windyfly.agent.capabilities import Band
from windyfly.agent.loop import agent_respond
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry
import windyfly.agent.context_header as _ch

from tests.test_agent_loop import _make_config


def _answer(content: str = "ok", tool_calls: list | None = None) -> dict:
    return {"content": content, "model": "m", "input_tokens": 1, "output_tokens": 1,
            "tool_calls": tool_calls}


def test_post_tool_model_call_carries_the_session_id():
    _ch._tracker = None
    db = Database(":memory:")
    wq = WriteQueue()
    wq.start()
    call = {"id": "c1", "type": "function", "function": {"name": "get_time", "arguments": "{}"}}
    try:
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", side_effect=[_answer("", [call]), _answer("done")]) as llm, \
             patch("windyfly.agent.loop._dispatch_tool_call", return_value='{"time": "noon"}'):
            agent_respond(_make_config(), db, wq, "what time is it?", "matrix:!room:s-42",
                          tool_registry=ToolRegistry(), band=Band.OWNER)
    finally:
        wq.stop()
    assert llm.call_count == 2
    assert [c.kwargs.get("session_id") for c in llm.call_args_list] == ["matrix:!room:s-42"] * 2
    db.close()
