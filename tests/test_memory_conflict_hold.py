"""Strand C4.6/C4.7: a contradicting memory update is HELD until the owner chooses.

Before: upsert_node logged a conflicts row and then overwrote the node anyway, so "keep old" could not
bring the old value back. Now the node keeps its value, the conflicts row holds the proposed one, and
the owner decides (prompt facts block on an owner turn, memory.resolve_conflict, /conflicts keep).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

from windyfly.agent.capabilities import Band
from windyfly.agent.capabilities.memory_search import register_memory_search_capabilities
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.agent.prompt import assemble_prompt
from windyfly.memory.conflict_detector import (
    get_pending_conflicts,
    get_unresolved_conflicts,
    resolve_conflict,
)
from windyfly.memory.database import Database
from windyfly.memory.nodes import upsert_node


def _meta(db: Database, name: str) -> dict:
    row = db.fetchone("SELECT metadata FROM nodes WHERE name = ?", (name,))
    assert row is not None
    return json.loads(row["metadata"])


def _db_with_conflict() -> tuple[Database, str]:
    db = Database(":memory:")
    upsert_node(db, "fact", "user_location", metadata={"value": "New York"},
                epistemic_status="user_stated", source="owner")
    upsert_node(db, "fact", "user_location", metadata={"value": "Boston"},
                epistemic_status="inferred", source="extractor")
    pending = get_pending_conflicts(db)
    assert len(pending) == 1
    return db, pending[0]["id"]


class TestHold:
    def test_contradicting_update_does_not_overwrite(self):
        db, _ = _db_with_conflict()
        assert _meta(db, "user_location") == {"value": "New York"}
        node = db.fetchone("SELECT source, epistemic_status FROM nodes WHERE name = 'user_location'")
        assert node is not None
        assert node["source"] == "owner"
        assert node["epistemic_status"] == "user_stated"

    def test_conflict_row_holds_the_proposed_value(self):
        db, cid = _db_with_conflict()
        row = db.fetchone("SELECT * FROM conflicts WHERE id = ?", (cid,))
        assert row is not None
        assert row["resolution_status"] == "pending"
        assert json.loads(row["new_value"]) == {"value": "Boston"}
        proposed = json.loads(row["proposed"])
        assert proposed["source"] == "extractor"
        assert proposed["epistemic_status"] == "inferred"

    def test_same_value_repeated_is_not_a_conflict(self):
        db = Database(":memory:")
        upsert_node(db, "fact", "pet", metadata={"name": "Rex", "kind": "dog"})
        # Same value, keys in another order: not a conflict.
        upsert_node(db, "fact", "pet", metadata={"kind": "dog", "name": "Rex"})
        assert get_unresolved_conflicts(db) == []

    def test_repeating_a_held_proposal_does_not_add_a_second_row(self):
        db, _ = _db_with_conflict()
        upsert_node(db, "fact", "user_location", metadata={"value": "Boston"}, source="extractor")
        assert len(get_pending_conflicts(db)) == 1
        assert _meta(db, "user_location") == {"value": "New York"}

    def test_agent_records_are_rewritten_not_held(self):
        """A turnover letter is the agent's own note: the newest one wins, nothing to ask the owner."""
        db = Database(":memory:")
        upsert_node(db, "turnover_letter", "turnover:cli:1", metadata={"summary": "first session"})
        upsert_node(db, "turnover_letter", "turnover:cli:1", metadata={"summary": "second session"})
        assert _meta(db, "turnover:cli:1") == {"summary": "second session"}
        assert get_unresolved_conflicts(db) == []


class TestResolve:
    def test_keep_old_keeps_the_old_value(self):
        db, cid = _db_with_conflict()
        out = resolve_conflict(db, cid, keep_new=False, resolved_by="owner (test)")
        assert out["ok"] is True
        assert _meta(db, "user_location") == {"value": "New York"}
        row = db.fetchone("SELECT * FROM conflicts WHERE id = ?", (cid,))
        assert row is not None
        assert row["resolution_status"] == "user_resolved"
        assert row["kept"] == "old"
        assert row["resolved_by"] == "owner (test)"
        assert row["resolved_at"]
        assert get_pending_conflicts(db) == []

    def test_keep_new_applies_the_held_value(self):
        db, cid = _db_with_conflict()
        out = resolve_conflict(db, cid, keep_new=True, resolved_by="owner (test)")
        assert out["ok"] is True
        assert _meta(db, "user_location") == {"value": "Boston"}
        node = db.fetchone("SELECT source, epistemic_status FROM nodes WHERE name = 'user_location'")
        assert node is not None
        assert node["source"] == "extractor"
        row = db.fetchone("SELECT kept FROM conflicts WHERE id = ?", (cid,))
        assert row is not None and row["kept"] == "new"

    def test_resolve_by_short_id_and_twice_is_refused(self):
        db, cid = _db_with_conflict()
        assert resolve_conflict(db, cid[:8], keep_new=False)["ok"] is True
        again = resolve_conflict(db, cid[:8], keep_new=True)
        assert again["ok"] is False
        assert _meta(db, "user_location") == {"value": "New York"}

    def test_unknown_id(self):
        db = Database(":memory:")
        assert resolve_conflict(db, "nope1234", keep_new=True)["ok"] is False


def _system_text(msgs: list[dict]) -> str:
    return "\n".join(m["content"] for m in msgs if m["role"] == "system")


class TestPromptFacts:
    _CFG = {"agent": {"name": "Fly"}, "personality": {}}

    def test_owner_turn_carries_the_pending_conflict(self):
        db, cid = _db_with_conflict()
        text = _system_text(assemble_prompt(self._CFG, db, "hello", "s1", band=Band.OWNER))
        assert "Unresolved memory conflicts (the owner has not chosen yet)" in text
        assert f"#{cid[:8]} user_location" in text
        assert "New York" in text and "Boston" in text

    def test_legacy_caller_without_band_is_owner(self):
        db, cid = _db_with_conflict()
        text = _system_text(assemble_prompt(self._CFG, db, "hello", "s1"))
        assert f"#{cid[:8]}" in text

    def test_non_owner_bands_never_see_it(self):
        db, cid = _db_with_conflict()
        for band in (Band.SANDBOX, Band.USER, Band.TRUSTED):
            text = _system_text(assemble_prompt(self._CFG, db, "hello", "s1", band=band))
            assert "memory conflicts" not in text, band
            assert "Boston" not in text, band

    def test_capped_at_three_newest_first(self):
        db = Database(":memory:")
        for i in range(5):
            upsert_node(db, "fact", f"k{i}", metadata={"v": "alpha"})
            upsert_node(db, "fact", f"k{i}", metadata={"v": "omega"})
        assert len(get_pending_conflicts(db)) == 5
        text = _system_text(assemble_prompt(self._CFG, db, "hello", "s1", band=Band.OWNER))
        block = text.split("Unresolved memory conflicts", 1)[1]
        assert block.count("\n- #") == 3
        assert "2 more" in block

    def test_resolved_conflict_leaves_the_prompt(self):
        db, cid = _db_with_conflict()
        resolve_conflict(db, cid, keep_new=False)
        text = _system_text(assemble_prompt(self._CFG, db, "hello", "s1", band=Band.OWNER))
        assert "memory conflicts" not in text


class TestOwnerSurfaces:
    def test_tool_is_owner_band_and_resolves(self):
        db, cid = _db_with_conflict()
        reg = CapabilityRegistry()
        register_memory_search_capabilities(reg, db, {})
        cap = reg.get("memory.resolve_conflict")
        assert cap is not None
        assert cap.band_required == Band.OWNER
        assert cap.audit_required is True
        assert "memory.resolve_conflict" not in [
            s["function"]["name"] for s in reg.tool_schemas_for_band(Band.TRUSTED)
        ]
        out = reg.invoke_sync("memory.resolve_conflict", {"conflict_id": cid[:8], "keep": "new"}, Band.OWNER)
        assert out["ok"] is True
        assert _meta(db, "user_location") == {"value": "Boston"}

    def test_tool_rejects_a_bad_keep(self):
        db, cid = _db_with_conflict()
        reg = CapabilityRegistry()
        register_memory_search_capabilities(reg, db, {})
        out = reg.invoke_sync("memory.resolve_conflict", {"conflict_id": cid, "keep": "both"}, Band.OWNER)
        assert out["ok"] is False
        assert _meta(db, "user_location") == {"value": "New York"}

    def test_slash_command_lists_and_resolves(self):
        from windyfly.commands import core
        from windyfly.commands.registry import parse_command, registry

        db, cid = _db_with_conflict()
        core.init_core(db=db, config={})

        def run(text: str) -> str:
            return asyncio.run(registry.execute(parse_command(text), {"platform": "cli"}))

        listing = run("/conflicts")
        assert f"#{cid[:8]}" in listing and "New York" in listing and "Boston" in listing
        reply = run(f"/conflicts keep {cid[:8]} old")
        assert "old" in reply.lower()
        assert _meta(db, "user_location") == {"value": "New York"}
        assert get_pending_conflicts(db) == []
        assert "No unresolved" in run("/conflicts")


class TestMigration:
    def test_existing_db_gains_the_columns_and_keeps_rows(self, tmp_path):
        path = tmp_path / "old.db"
        db = Database(str(path))
        db.execute("INSERT INTO conflicts (id, node_id, old_value, new_value) VALUES ('legacy1', 'n1', 'a', 'b')")
        db.commit()
        db.close()
        # Simulate a file from before migration 15: drop the new columns and the version row.
        conn = sqlite3.connect(str(path))
        for col in ("proposed", "kept", "resolution", "resolved_by"):
            conn.execute(f"ALTER TABLE conflicts DROP COLUMN {col}")
        conn.execute("DELETE FROM schema_version WHERE version >= 15")
        conn.commit()
        conn.close()

        db = Database(str(path))
        cols = {r["name"] for r in db.fetchall("PRAGMA table_info(conflicts)")}
        assert {"proposed", "kept", "resolution", "resolved_by"} <= cols
        ver = db.fetchone("SELECT MAX(version) AS v FROM schema_version")
        assert ver is not None and ver["v"] >= 15
        row = db.fetchone("SELECT * FROM conflicts WHERE id = 'legacy1'")
        assert row is not None and row["resolution_status"] == "unresolved"
        db.close()

    def test_legacy_already_applied_row_keep_old_restores(self):
        """A row written by the old code: the node already holds the new value; keep old puts the old back."""
        db = Database(":memory:")
        upsert_node(db, "fact", "city", metadata={"v": "Boston"})
        node = db.fetchone("SELECT id FROM nodes WHERE name = 'city'")
        assert node is not None
        db.execute(
            "INSERT INTO conflicts (id, node_id, old_value, new_value) VALUES ('legacy2', ?, ?, ?)",
            (node["id"], json.dumps({"v": "New York"}), json.dumps({"v": "Boston"})),
        )
        db.commit()
        assert resolve_conflict(db, "legacy2", keep_new=False)["ok"] is True
        assert _meta(db, "city") == {"v": "New York"}
