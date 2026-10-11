"""Strand event log (Wave 3, small PR): memory writes, held conflicts and surfaced intents are
recorded as events, with ids and types only. Declared types nothing emitted are gone (cost.log:
the cost ledger is the one record of cost).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from windyfly.memory.database import Database
from windyfly.memory.intents import create_intent, touch_intent
from windyfly.memory.nodes import upsert_node
from windyfly.observability.events import EVENT_TYPES, record_event

SRC = Path(__file__).resolve().parents[1] / "src" / "windyfly"


def events(db: Database, event_type: str) -> list[dict]:
    rows = db.fetchall("SELECT data FROM events WHERE event_type = ? ORDER BY id", (event_type,))
    return [json.loads(r["data"]) for r in rows]


def test_memory_write_records_insert_and_update_without_the_owners_words():
    db = Database(":memory:")
    node_id = upsert_node(db, "location", "user_location:Lisbon", metadata={"raw_statement": "I live in Lisbon"},
                          source="owner_stated", epistemic_status="user_stated")
    upsert_node(db, "location", "user_location:Lisbon", metadata={"raw_statement": "I live in Lisbon now"},
                source="owner_stated", epistemic_status="user_stated")

    rows = events(db, "memory.write")
    assert rows == [
        {"node_id": node_id, "node_type": "location", "op": "insert", "source": "owner_stated"},
        {"node_id": node_id, "node_type": "location", "op": "update", "source": "owner_stated"},
    ]
    raw = json.dumps(rows)
    assert "Lisbon" not in raw and "I live" not in raw


def test_a_held_conflict_is_recorded_once_and_is_not_a_memory_write():
    db = Database(":memory:")
    upsert_node(db, "fact", "user_location", metadata={"value": "New York"},
                epistemic_status="user_stated", source="owner")
    upsert_node(db, "fact", "user_location", metadata={"value": "Boston"},
                epistemic_status="inferred", source="extractor")
    upsert_node(db, "fact", "user_location", metadata={"value": "Boston"},   # same proposal again
                epistemic_status="inferred", source="extractor")

    conflicts = events(db, "conflict.detect")
    assert len(conflicts) == 1
    assert set(conflicts[0]) == {"conflict_id", "node_id", "node_type"}
    assert conflicts[0]["node_type"] == "fact"
    assert [r["op"] for r in events(db, "memory.write")] == ["insert"]   # the held value was not written
    assert "Boston" not in json.dumps(conflicts)


def test_intent_surface_on_create_and_touch_without_the_description():
    db = Database(":memory:")
    intent_id = create_intent(db, "book the dentist for Tuesday", origin="user_said")
    touch_intent(db, intent_id)

    assert events(db, "intent.surface") == [
        {"intent_id": intent_id, "op": "create", "origin": "user_said"},
        {"intent_id": intent_id, "op": "touch"},
    ]


def test_record_event_never_breaks_the_write_it_describes():
    db = Database(":memory:")
    db.execute("DROP TABLE events")
    record_event(db, "memory.write", {"node_id": "x"})   # no exception
    node_id = upsert_node(db, "fact", "k", metadata={"v": 1})
    assert db.fetchone("SELECT id FROM nodes WHERE id = ?", (node_id,))


def test_every_declared_event_type_is_emitted_somewhere():
    """A declared type nothing emits is a promise the log never keeps (cost.log, skill.evaluate,
    matrix.message, personality.change, email.outbound were)."""
    code = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")
                     if p.name != "events.py")
    emitted = set(re.findall(r'(?:log_event|record_event)\([^"]*"([a-z_]+(?:\.[a-z_]+)*)"', code, re.S))
    missing = sorted(t for t in EVENT_TYPES if t not in emitted)
    assert not missing, f"declared but never emitted: {missing}"
