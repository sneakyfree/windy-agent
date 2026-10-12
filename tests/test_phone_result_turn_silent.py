"""A phone result turn posts NOTHING while the agent is paused or Mind's switch cannot be read
(phone-tools 1.1.0 clause 4; Hub 10-12, R7).

A tool_result from the owner's phone starts one tool-less owner turn. If the owner has the agent
stopped in Windy Mind, or Mind cannot read that switch, the turn must not reach a model AND must
not post anything: the person did not just speak, so even the plain stop line would be the agent
talking on its own. (The result is still read once and redacted by the poller, unchanged:
tests/test_phone_tools.py.) A person who writes while the agent is stopped still gets the plain
line, as before.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["WINDYFLY_PROVIDERS_PATH"] = "/tmp/windyfly-test-no-such-file.json"

from windyfly.agent import loop, models  # noqa: E402
from windyfly.channels import phone_tools  # noqa: E402


def _respond(monkeypatch, *, result_turn: bool, paused: bool = False, unreadable: bool = False,
             session: str = "sess-phone") -> str:
    from windyfly.agent.loop import agent_respond
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue
    from windyfly.tools.registry import ToolRegistry

    loop._reset_pause_unreadable_for_tests()
    monkeypatch.setattr(loop, "is_online", lambda: True)
    monkeypatch.setattr(models, "check_owner_pause",
                        lambda force=False: {"reason": "stopped by owner"} if paused else None)
    cfg = {"agent": {"default_model": "claude-sonnet-4-6"}, "memory": {}, "personality": {"preset": "buddy"}}
    side = models.MindPauseUnreadable() if unreadable else AssertionError("no model call")
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "p.db"))
        wq = WriteQueue()
        wq.start()
        try:
            if result_turn:
                phone_tools.mark_data_turn(session, '{"contacts": 3}')
            with patch("windyfly.agent.loop.call_llm", side_effect=side):
                return agent_respond(cfg, db, wq, "[Phone result: found 3 contacts]", session, ToolRegistry())
        finally:
            phone_tools.clear_turn(session)
            wq.stop()
            db.close()


def test_a_result_turn_posts_nothing_while_paused(monkeypatch):
    assert _respond(monkeypatch, result_turn=True, paused=True) == ""


def test_a_result_turn_posts_nothing_when_the_switch_is_unreadable(monkeypatch):
    assert _respond(monkeypatch, result_turn=True, unreadable=True) == ""


def test_a_person_still_hears_the_plain_lines(monkeypatch):
    assert _respond(monkeypatch, result_turn=False, paused=True) == loop.owner_paused_reply()
    assert _respond(monkeypatch, result_turn=False, unreadable=True) == loop.PAUSE_UNREADABLE_REPLY


def test_the_phone_loop_posts_nothing_for_an_empty_answer(monkeypatch):
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot

    bot = MagicMock()
    bot._room_sessions = {}
    bot.client.room_send = AsyncMock()
    for reply, posts in (("", 0), ("Found 3 contacts named Ann.", 1)):
        bot.client.room_send.reset_mock()
        monkeypatch.setattr("windyfly.agent.executor.run_turn", AsyncMock(return_value=reply))
        asyncio.run(WindyFlyMatrixBot._run_result_turn(bot, "!dm:x", "[stub]", "data"))
        assert bot.client.room_send.await_count == posts
