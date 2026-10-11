"""Facts and goals come only from the owner's own words (Hub rulings, 2026-10-11).

Before: "my name is X / I live in Y" from ANY sender became a fact about the
owner (epistemic_status user_stated, which the prompt treats as trusted). Now a
paired agent, another user or a turn that read untrusted content stores nothing.
"""

from __future__ import annotations

from unittest.mock import patch

from windyfly.agent.capabilities import Band
from windyfly.agent.loop import UNTRUSTED_SOURCE_TOOLS, _extract_and_store_facts, agent_respond
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry
import windyfly.agent.context_header as _ch

from tests.test_agent_loop import _make_config

_MSG = "My name is Mallory. I live in Atlantis. I need to buy a boat tomorrow."


def _answer(content: str = "ok", tool_calls: list | None = None) -> dict:
    return {"content": content, "model": "m", "input_tokens": 1, "output_tokens": 1,
            "tool_calls": tool_calls}


def _facts(db: Database) -> list[dict]:
    return db.fetchall("SELECT type, name, source FROM nodes WHERE type IN ('person', 'location')")


def _intents(db: Database) -> list[dict]:
    return db.fetchall("SELECT description FROM intents")


def _turn(band, llm_answers, tool_registry=None) -> Database:
    _ch._tracker = None
    db = Database(":memory:")
    wq = WriteQueue()
    wq.start()
    try:
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", side_effect=llm_answers), \
             patch("windyfly.agent.loop._dispatch_tool_call", return_value='{"items": []}'):
            agent_respond(_make_config(), db, wq, _MSG, "s-facts",
                          tool_registry=tool_registry, band=band)
    finally:
        wq.stop()
    return db


class TestExtractorFailsClosed:
    def test_without_owner_flag_nothing_is_queued(self):
        class _Capture:
            calls: list = []

            def enqueue(self, *a, **k):
                self.calls.append(k)

        q = _Capture()
        _extract_and_store_facts(Database(":memory:"), q, _MSG)
        assert q.calls == []

    def test_owner_flag_stores_owner_stated(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        _extract_and_store_facts(db, wq, _MSG, owner=True)
        wq.stop()
        rows = _facts(db)
        assert {r["source"] for r in rows} == {"owner_stated"} and len(rows) == 2
        db.close()


class TestTurns:
    def test_non_owner_turns_store_no_fact_and_no_conflict(self):
        for band in (Band.SANDBOX, Band.USER, Band.TRUSTED):
            db = _turn(band, [_answer()])
            assert _facts(db) == [], band
            assert db.fetchall("SELECT id FROM conflicts") == [], band
            assert _intents(db) == [], band
            db.close()

    def test_non_owner_cannot_refresh_an_owner_goal(self):
        from windyfly.memory.intents import create_intent

        _ch._tracker = None
        db = Database(":memory:")
        iid = create_intent(db, "buy a boat tomorrow")
        db.execute("UPDATE intents SET decay_score = 0.5, last_touched = datetime('now', '-20 days') "
                   "WHERE id = ?", (iid,))
        db.commit()
        wq = WriteQueue()
        wq.start()
        try:
            with patch("windyfly.agent.loop.is_online", return_value=True), \
                 patch("windyfly.agent.loop.call_llm", side_effect=[_answer()]):
                agent_respond(_make_config(), db, wq, _MSG, "s-facts", band=Band.USER)
        finally:
            wq.stop()
        row = db.fetchone("SELECT decay_score FROM intents WHERE id = ?", (iid,))
        assert row is not None and row["decay_score"] == 0.5
        db.close()

    def test_owner_turn_stores_the_facts(self):
        db = _turn(Band.OWNER, [_answer()])
        names = sorted(r["name"] for r in _facts(db))
        assert names == ["user_location:Atlantis", "user_name:Mallory"]
        assert len(_intents(db)) == 1
        db.close()

    def test_tainted_owner_turn_stores_nothing(self):
        source_tool = sorted(UNTRUSTED_SOURCE_TOOLS)[0]
        call = {"id": "c1", "type": "function",
                "function": {"name": source_tool, "arguments": "{}"}}
        db = _turn(Band.OWNER, [_answer("", [call]), _answer("done")], tool_registry=ToolRegistry())
        assert _facts(db) == []
        assert _intents(db) == []
        db.close()
