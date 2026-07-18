"""The Journal — dated index over the Chronicle (Doctrine Build 3)."""
from __future__ import annotations

import json

from windyfly.memory.database import Database
from windyfly.memory import journal


def _ep(db, i, role, content, ts):
    db.execute(
        "INSERT INTO episodes (id, role, content, session_id, created_at) "
        "VALUES (?, ?, ?, 'telegram:1:v1', ?)",
        (f"e{i}", role, content, ts),
    )


def _db_with_day(day="2026-07-17"):
    db = Database(":memory:")
    # Chapter 1 (morning)
    _ep(db, 1, "user", "Let's plan the quilt raffle for the county fair.", f"{day} 09:00:00")
    _ep(db, 2, "assistant", "Great — what should tickets cost?", f"{day} 09:01:00")
    _ep(db, 3, "user", "Five dollars. And ask Fred at the dealership.", f"{day} 09:02:00")
    # Chapter 2 (evening — >1h gap)
    _ep(db, 4, "user", "Back — what's the weather tomorrow?", f"{day} 20:00:00")
    _ep(db, 5, "assistant", "Clear and cool.", f"{day} 20:01:00")
    db.commit()
    return db


def _fake_model(messages, *, max_tokens=700):
    # Pretend the LLM returned a clean journal JSON.
    return json.dumps({
        "summary": "Planned the county-fair quilt raffle and checked weather.",
        "bullets": ["Quilt raffle tickets set at $5", "Ask Fred at the dealership"],
        "entities": ["Fred", "county fair", "quilt raffle"],
    })


class TestChaptering:
    def test_idle_gap_splits_into_two_chapters(self):
        db = _db_with_day()
        entries = journal.compose_day_entries(db, "2026-07-17")
        assert len(entries) == 2
        assert entries[0]["turn_count"] == 3
        assert entries[1]["turn_count"] == 2
        assert entries[0]["chapter"] == 0 and entries[1]["chapter"] == 1

    def test_empty_day_returns_nothing(self):
        db = Database(":memory:")
        assert journal.compose_day_entries(db, "2026-07-17") == []


class TestEnrichmentAndFallback:
    def test_model_enriched_entry(self):
        db = _db_with_day()
        entries = journal.compose_day_entries(
            db, "2026-07-17", model_caller=_fake_model,
        )
        e = entries[0]
        assert e["enriched"] is True
        assert "quilt raffle" in e["summary"].lower()
        assert "Fred" in e["entities"]
        assert len(e["bullets"]) >= 1

    def test_deterministic_skeleton_when_no_model(self):
        db = _db_with_day()
        entries = journal.compose_day_entries(db, "2026-07-17")  # no model
        e = entries[0]
        assert e["enriched"] is False
        assert e["bullets"]  # crude bullets from user turns still present
        assert "turns" in e["summary"]

    def test_broken_model_falls_back_not_crash(self):
        db = _db_with_day()

        def boom(messages, *, max_tokens=700):
            raise RuntimeError("model down")

        entries = journal.compose_day_entries(
            db, "2026-07-17", model_caller=boom,
        )
        assert entries[0]["enriched"] is False  # skeleton, no crash


class TestWriteAndRead:
    def test_write_is_idempotent(self):
        db = _db_with_day()
        n1 = journal.write_day(db, "2026-07-17", model_caller=_fake_model)
        n2 = journal.write_day(db, "2026-07-17", model_caller=_fake_model)
        assert n1 == 2 and n2 == 2
        rows = db.fetchall(
            "SELECT COUNT(*) AS c FROM nodes WHERE type='chronicle_journal'"
        )
        assert rows[0]["c"] == 2  # upsert, not duplicate

    def test_read_entries_newest_first_with_fields(self):
        db = _db_with_day()
        journal.write_day(db, "2026-07-17", model_caller=_fake_model)
        entries = journal.read_entries(db)
        assert len(entries) == 2
        assert entries[0]["date"] == "2026-07-17"
        assert "summary" in entries[0] and "bullets" in entries[0]
        assert "entities" in entries[0]

    def test_write_never_raises_on_broken_db(self):
        class Broken:
            def fetchall(self, *a, **k):
                raise RuntimeError("toast")
        assert journal.write_day(Broken(), "2026-07-17") == 0


class TestJournalReadCapability:
    def test_capability_registered_and_reads(self):
        from windyfly.agent.capabilities.registry import CapabilityRegistry
        from windyfly.agent.capabilities.memory_search import (
            register_memory_search_capabilities,
        )
        db = _db_with_day()
        journal.write_day(db, "2026-07-17", model_caller=_fake_model)
        reg = CapabilityRegistry()
        register_memory_search_capabilities(reg, db, {})
        cap = reg.get("journal.read")
        assert cap is not None
        out = cap.handler(limit=10)
        assert out["ok"] is True
        assert out["count"] == 2


class TestNodeTypeIsolation:
    """Live-caught 2026-07-18: the Chronicle Journal must NOT collide
    with the agent's pre-existing self-reflective 'journal_entry' diary
    nodes. read_entries reads only 'chronicle_journal'."""

    def test_read_ignores_reflective_journal_entry_nodes(self):
        from windyfly.memory.nodes import upsert_node
        db = _db_with_day()
        journal.write_day(db, "2026-07-17", model_caller=_fake_model)
        # A pre-existing reflective diary node (different organ):
        upsert_node(
            db, type="journal_entry",
            name="journal:Tonight was quiet, just a moon emoji",
            metadata={"session_id": "abc", "emotional_context": "neutral"},
        )
        entries = journal.read_entries(db)
        # Only the 2 dated Chronicle-Journal chapters, no None-dated junk
        assert len(entries) == 2
        assert all(e["date"] == "2026-07-17" for e in entries)
