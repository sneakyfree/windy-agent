"""A failure AFTER the answer is saved never loses it (Hub, after #699).

The turn saves the person's message and the answer (step 3), then extracts facts (5) and detects an intent (6, with its
own model fallback). Either raising used to raise out of agent_respond: the person got no answer although it was
already in memory, and on a first contact the welcome's fallback then saved the message a second time."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from tests.test_first_contact_welcome import _make_config
from windyfly.agent.loop import agent_respond
from windyfly.agent.welcome import WELCOME_TEXT
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

REPLY = {"content": "Here is your answer.", "model": "claude-haiku-4-5-20251001", "input_tokens": 10,
         "output_tokens": 5, "tool_calls": None}


@pytest.fixture
def stack():
    db = Database(":memory:")
    wq = WriteQueue()
    wq.start()
    yield _make_config(), db, wq
    wq.stop()
    db.close()


def _episodes(db):
    time.sleep(0.5)  # the write queue lands the episodes
    # the write queue gives no order between same-priority writes: compare the set, counted
    return sorted((r["role"], r["content"]) for r in db.fetchall("SELECT role, content FROM episodes"))


@pytest.mark.parametrize("broken", ["windyfly.agent.loop._extract_and_store_facts", "windyfly.agent.loop.detect_intent"])
@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm", return_value=REPLY)
def test_a_failure_after_the_save_still_returns_the_saved_answer(_llm, _online, stack, broken):
    config, db, wq = stack
    with patch(broken, side_effect=RuntimeError("tail broke")):
        reply = agent_respond(config, db, wq, "What's on today?", "s-tail")
    assert reply == "Here is your answer."
    assert _episodes(db) == [("assistant", "Here is your answer."), ("user", "What's on today?")]


@pytest.mark.virgin_db_welcome
@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm", return_value=REPLY)
def test_on_a_first_contact_the_tail_failing_neither_loses_the_answer_nor_saves_the_message_twice(_llm, _online, stack):
    config, db, wq = stack
    with patch("windyfly.agent.loop.detect_intent", side_effect=RuntimeError("tail broke")):
        reply = agent_respond(config, db, wq, "Hello, are you there?", "s-first")
    assert reply == WELCOME_TEXT + "\n\nHere is your answer."
    rows = _episodes(db)
    assert [r for r in rows if r[0] == "user"] == [("user", "Hello, are you there?")]
    assert rows == [("assistant", "Here is your answer."), ("user", "Hello, are you there?")]


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm", return_value=REPLY)
def test_the_failure_is_logged_by_its_type_only(_llm, _online, stack, caplog):
    config, db, wq = stack
    with patch("windyfly.agent.loop.detect_intent", side_effect=RuntimeError("SECRET-CONTENT-IN-THE-MESSAGE")):
        agent_respond(config, db, wq, "remember my PIN is 1234", "s-log")
    assert "RuntimeError" in caplog.text
    assert "SECRET-CONTENT-IN-THE-MESSAGE" not in caplog.text and "1234" not in caplog.text
