"""Tests for agent journal."""

from windyfly.memory.database import Database
from windyfly.memory.nodes import get_nodes_by_type, upsert_node


class TestJournal:
    def test_journal_entry_stored(self):
        db = Database(":memory:")
        upsert_node(
            db, "journal_entry", "journal:Had a great debugging session",
            metadata={"entry": "Had a great debugging session", "emotional_context": "excited"},
            source="agent_journal", epistemic_status="verified",
        )
        entries = get_nodes_by_type(db, "journal_entry", limit=5)
        assert len(entries) >= 1
        db.close()

    def test_journal_in_dashboard(self):
        from windyfly.dashboard.data import get_dashboard_summary
        db = Database(":memory:")
        upsert_node(
            db, "journal_entry", "journal:Test entry",
            metadata={"entry": "Test entry", "emotional_context": "neutral"},
            source="agent_journal", epistemic_status="verified",
        )
        summary = get_dashboard_summary(db)
        assert "journal" in summary
        assert len(summary["journal"]) >= 1
        db.close()
