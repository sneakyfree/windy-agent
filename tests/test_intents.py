"""Tests for the intent system (CRUD, detection, decay)."""

from __future__ import annotations

from windyfly.agent.intent_detector import detect_intent
from windyfly.memory.database import Database
from windyfly.memory.intents import (
    complete_intent,
    create_intent,
    get_intent,
    pause_intent,
    surface_pending_intents,
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

    def test_schema_version_is_2(self):
        db = Database(":memory:")
        row = db.fetchone("SELECT MAX(version) as v FROM schema_version")
        assert row["v"] == 2
        db.close()
