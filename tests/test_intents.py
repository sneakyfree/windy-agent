"""Tests for the intent system (CRUD, detection, decay)."""

from __future__ import annotations

import json
import threading
import time

import pytest

from windyfly.agent import intent_detector
from windyfly.agent.intent_detector import detect_intent
from windyfly.memory.database import Database
from windyfly.memory.intents import (
    complete_intent,
    create_intent,
    get_intent,
    pause_intent,
    surface_pending_intents,
    touch_intent,
)


class TestIntentCRUD:
    def test_create_and_get(self):
        db = Database(":memory:")
        iid = create_intent(db, "Learn Spanish")
        intent = get_intent(db, iid)
        assert intent is not None
        assert intent["description"] == "Learn Spanish"
        assert intent["status"] == "active"
        db.close()

    def test_complete_intent(self):
        db = Database(":memory:")
        iid = create_intent(db, "Buy groceries")
        complete_intent(db, iid)
        intent = get_intent(db, iid)
        assert intent["status"] == "completed"
        db.close()

    def test_pause_intent(self):
        db = Database(":memory:")
        iid = create_intent(db, "Read a book")
        pause_intent(db, iid)
        intent = get_intent(db, iid)
        assert intent["status"] == "paused"
        db.close()

    def test_surface_inferred(self):
        db = Database(":memory:")
        create_intent(db, "User seems to want coffee", origin="inferred_from_chat")
        create_intent(db, "Explicit goal", origin="user_said")
        pending = surface_pending_intents(db)
        assert len(pending) == 1
        assert pending[0]["origin"] == "inferred_from_chat"
        db.close()


class TestIntentDetection:
    def test_detect_want(self):
        result = detect_intent("I want to learn Python")
        assert result is not None
        assert result["has_intent"] is True
        assert "learn Python" in result["description"]

    def test_detect_need(self):
        result = detect_intent("I need a vacation")
        assert result is not None
        assert result["has_intent"] is True

    def test_detect_remind(self):
        result = detect_intent("Remind me to call the dentist")
        assert result is not None
        assert "call the dentist" in result["description"]

    def test_no_intent_in_question(self):
        result = detect_intent("How's the weather?")
        assert result is None

    def test_no_intent_in_greeting(self):
        result = detect_intent("Hi there!")
        assert result is None


class TestMigrationV2:
    def test_intents_table_exists(self):
        db = Database(":memory:")
        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = {t["name"] for t in tables}
        assert "intents" in table_names
        assert "edges" in table_names
        assert "conflicts" in table_names
        assert "soul_history" in table_names
        db.close()

    def test_schema_version_is_current(self):
        db = Database(":memory:")
        row = db.fetchone("SELECT MAX(version) as v FROM schema_version")
        assert row["v"] >= 7  # >= so future migrations do not retroactively break (was: == 7 for Wave 14 tracing spine)
        db.close()


class TestLLMIntentPath:
    """The LLM inference path (origin 'inferred_from_chat') feeds the
    Intent Inbox. It is opt-in: WINDY_INTENT_LLM=1."""

    _MSG = "Thinking about maybe getting into woodworking this winter"

    def test_prompt_formats_without_error(self):
        # Pre-fix: the JSON example's bare braces made str.format raise
        # KeyError('"has_intent"'), silently killing the LLM path.
        prompt = intent_detector._build_llm_prompt("hello there")
        assert "hello there" in prompt
        assert '{"has_intent": true/false' in prompt

    def test_llm_off_by_default(self, monkeypatch):
        monkeypatch.delenv("WINDY_INTENT_LLM", raising=False)
        calls: list[object] = []

        def _fake_call_llm(messages, **kwargs):
            calls.append(messages)
            return {"content": "{}"}

        monkeypatch.setattr("windyfly.agent.models.call_llm", _fake_call_llm)
        assert detect_intent(self._MSG, proactivity=10) is None
        assert calls == []

    def test_llm_answer_becomes_inbox_intent(self, monkeypatch):
        monkeypatch.setenv("WINDY_INTENT_LLM", "1")
        seen: list[list[dict[str, str]]] = []

        def _fake_call_llm(messages, **kwargs):
            seen.append(messages)
            return {"content": "```json\n" + json.dumps(
                {"has_intent": True, "description": "Take up woodworking this winter"}
            ) + "\n```"}

        monkeypatch.setattr("windyfly.agent.models.call_llm", _fake_call_llm)
        result = detect_intent(self._MSG, proactivity=5)
        assert len(seen) == 1
        assert self._MSG in seen[0][-1]["content"]
        assert result == {
            "has_intent": True,
            "description": "Take up woodworking this winter",
            "origin": "inferred_from_chat",
        }

        db = Database(":memory:")
        create_intent(db, result["description"], origin=result["origin"])
        pending = surface_pending_intents(db)
        assert [p["description"] for p in pending] == ["Take up woodworking this winter"]
        db.close()

    def test_llm_not_called_below_proactivity_5(self, monkeypatch):
        monkeypatch.setenv("WINDY_INTENT_LLM", "1")

        def _boom(messages, **kwargs):
            raise AssertionError("LLM must not be called")

        monkeypatch.setattr("windyfly.agent.models.call_llm", _boom)
        assert detect_intent(self._MSG, proactivity=4) is None


class TestTouchIntent:
    """Mentioning an active intent again keeps it fresh, so decay never
    pauses a goal the owner keeps bringing up."""

    def _age(self, db, intent_id, days, score):
        db.execute(
            "UPDATE intents SET decay_score = ?, "
            "last_touched = datetime('now', ?) WHERE id = ?",
            (score, f"-{days} days", intent_id),
        )
        db.commit()

    def test_touch_resets_score_and_clock(self):
        db = Database(":memory:")
        iid = create_intent(db, "Learn to bake bread")
        self._age(db, iid, 20, 0.4)
        touch_intent(db, iid)
        row = get_intent(db, iid)
        assert row is not None and row["decay_score"] == 1.0
        recent = db.fetchone(
            "SELECT last_touched > datetime('now', '-1 minute') AS fresh FROM intents WHERE id = ?",
            (iid,),
        )
        assert recent is not None and recent["fresh"] == 1
        db.close()

    def test_touch_does_not_revive_a_paused_intent(self):
        db = Database(":memory:")
        iid = create_intent(db, "Old plan")
        pause_intent(db, iid)
        self._age(db, iid, 40, 0.2)
        touch_intent(db, iid)
        row = get_intent(db, iid)
        assert row is not None and row["status"] == "paused" and row["decay_score"] == 0.2
        db.close()

    def test_touched_intent_survives_the_decay_run(self):
        from windyfly.memory.intents import decay_intents
        from windyfly.memory.write_queue import WriteQueue

        db = Database(":memory:")
        kept = create_intent(db, "Goal mentioned again")
        stale = create_intent(db, "Goal never mentioned")
        self._age(db, kept, 30, 0.31)
        self._age(db, stale, 30, 0.31)
        touch_intent(db, kept)

        wq = WriteQueue()
        wq.start()
        try:
            decay_intents(db, wq)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                row = get_intent(db, stale)
                if row is not None and row["status"] == "paused":
                    break
                time.sleep(0.05)
        finally:
            wq.stop()

        stale_row, kept_row = get_intent(db, stale), get_intent(db, kept)
        assert stale_row is not None and stale_row["status"] == "paused"
        assert kept_row is not None and kept_row["status"] == "active"
        assert kept_row["decay_score"] == 1.0
        db.close()


class _StopLoop(BaseException):
    pass


class TestDecaySchedulerRunsIntentDecay:
    def test_scheduler_decays_and_pauses_stale_intents(self, tmp_path, monkeypatch):
        from windyfly import main as wf_main

        db_path = str(tmp_path / "decay.db")
        db = Database(db_path)
        stale = create_intent(db, "Old forgotten goal")
        fresh = create_intent(db, "Fresh goal")
        db.execute(
            "UPDATE intents SET decay_score = 0.31, "
            "last_touched = datetime('now', '-30 days') WHERE id = ?",
            (stale,),
        )
        db.commit()

        # Keep the cycle to the decay steps: no drift/curation/retention/backup.
        monkeypatch.setattr(
            "windyfly.personality.versioning.run_periodic_drift_check",
            lambda *a, **k: {"drift_detected": False},
        )
        monkeypatch.setattr("windyfly.skills.curator.run_curation", lambda *a, **k: {})
        monkeypatch.setattr("windyfly.memory.retention.run_retention", lambda *a, **k: {})

        async def _no_backup(config):
            return None

        monkeypatch.setattr("windyfly.cloud_backup.run_backup_if_due", _no_backup)

        real_sleep = time.sleep

        def _stop_sleep(seconds):
            # Only the 24h wait ends the loop; any other sleep is real.
            if seconds == wf_main._DECAY_INTERVAL_SECONDS:
                raise _StopLoop()
            real_sleep(seconds)

        monkeypatch.setattr(wf_main.time, "sleep", _stop_sleep)
        monkeypatch.setattr(threading, "excepthook", lambda args: None)

        t = wf_main._start_decay_scheduler({}, db_path)
        t.join(timeout=10)
        assert not t.is_alive()

        # decay_intents goes through the (LOW priority) write queue.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = get_intent(db, stale)
            if row and row["status"] == "paused":
                break
            real_sleep(0.05)
        row = get_intent(db, stale)
        assert row is not None and row["status"] == "paused"
        assert row["decay_score"] == pytest.approx(0.31 * 0.95)
        fresh_row = get_intent(db, fresh)
        assert fresh_row is not None and fresh_row["status"] == "active"
        db.close()
