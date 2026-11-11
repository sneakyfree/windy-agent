"""Strand v2, Part 5 Phases 9-10: relationship moments, the agent journal, self-assessment and the
bridge API (docs/strand/STRAND_V2.md). In-memory DBs, the model is a stub; no network."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

from windyfly.agent import loop
from windyfly.control_panel import set_slider
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

ROOT = Path(__file__).resolve().parents[2]


def _nodes(db, node_type):
    return db.fetchall("SELECT name FROM nodes WHERE type = ?", (node_type,))


def _turns(fn, *calls, reply, db=None):
    """Run fn(db, wq, {}, *args) for each args tuple with the model stubbed; return (db, model calls)."""
    db, wq = db or Database(":memory:"), WriteQueue()
    wq.start()
    with patch.object(loop, "call_llm", return_value={"content": reply}) as model:
        for args in calls:
            fn(db, wq, {}, *args)
    wq.stop()
    return db, model.call_count


# ── Phase 9 ──────────────────────────────────────────────────────────────────────────────

def test_p5_92_a_moment_is_saved_after_an_emotional_turn():
    db, calls = _turns(loop._extract_relationship_moment,
                       ("This bug is driving me mad", "Let's fix it together", "stressed", "s1"),
                       reply="frustrated → we fixed the bug together → relief")
    try:
        rows = _nodes(db, "relationship_moment")
    finally:
        db.close()
    assert calls == 1
    assert [r["name"] for r in rows] == ["moment:frustrated → we fixed the bug together → relief"]


def test_p5_93_a_journal_entry_is_written_on_the_tenth_turn_of_a_session():
    turn = ("hello", "hi there", "neutral", f"s-{uuid.uuid4()}")
    db, calls = _turns(loop._maybe_write_journal_entry, *([turn] * 9), reply="We talked about the weather.")
    try:
        assert calls == 0 and _nodes(db, "journal_entry") == []           # turns 1-9: nothing
        db, calls = _turns(loop._maybe_write_journal_entry, turn, reply="We talked about the weather.", db=db)
        rows = _nodes(db, "journal_entry")
    finally:
        db.close()
    assert calls == 1 and [r["name"] for r in rows] == ["journal:We talked about the weather."]


def test_p5_93_sessions_do_not_share_the_count():
    a, b = f"a-{uuid.uuid4()}", f"b-{uuid.uuid4()}"
    calls_ = [("hi", "hello", "neutral", a)] * 9 + [("hi", "hello", "neutral", b)]
    db, calls = _turns(loop._maybe_write_journal_entry, *calls_, reply="A quiet chat today.")
    db.close()
    assert calls == 0                                                     # 9 + 1, never 10 in one session


def test_p5_93_an_emotional_turn_is_journaled_at_once():
    db, calls = _turns(loop._maybe_write_journal_entry,
                       ("I got the job!", "Congratulations!", "excited", f"s-{uuid.uuid4()}"),
                       reply="They got the job and were thrilled.")
    try:
        rows = _nodes(db, "journal_entry")
    finally:
        db.close()
    assert calls == 1 and len(rows) == 1


# ── Phase 10: the bridge API ───────────────────────────────────────────────────────────────

def _moment_calls_at(sensitivity: int) -> int:
    """One full emotional turn through agent_respond with the slider at `sensitivity`; how many
    times the moment extractor ran."""
    from tests.test_strand_wave4_agent_loop import _llm_result, _make_config
    from windyfly.memory.episodes import save_episode

    db = Database(":memory:")
    save_episode(db, "user", "bootstrap", session_id="bootstrap")  # no brand-new-bot welcome
    wq = WriteQueue()
    wq.start()
    set_slider(db, "emotional_sensitivity", sensitivity)
    try:
        with patch.object(loop, "call_llm", return_value=_llm_result("That sounds hard. I'm here.")), \
             patch.object(loop, "is_online", return_value=True), \
             patch.object(loop, "detect_emotional_context", return_value="stressed"), \
             patch.object(loop, "_extract_relationship_moment") as extract:
            loop.agent_respond(_make_config(), db, wq, "Everything is going wrong today", f"m-{uuid.uuid4()}")
        return extract.call_count
    finally:
        wq.stop()
        db.close()


def test_p5_92_moments_are_captured_only_at_emotional_sensitivity_3_or_more():
    assert _moment_calls_at(2) == 0
    assert _moment_calls_at(3) == 1
