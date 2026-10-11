"""Strand v2, Part 3 gene 4.2: cognitive decay (docs/strand/STRAND_V2.md). Temp DB, no network."""

from __future__ import annotations

from windyfly.memory.database import Database
from windyfly.memory.decay import run_decay
from windyfly.memory.nodes import upsert_node
from windyfly.memory.write_queue import WriteQueue


def test_p3_42_faded_memories_become_speculative_but_are_kept(tmp_path):
    db = Database(str(tmp_path / "d.db"))
    try:
        faded = upsert_node(db, "fact", "faded", epistemic_status="user_stated")
        fresh = upsert_node(db, "fact", "fresh", epistemic_status="user_stated")
        db.execute("UPDATE nodes SET decay_score = 0.19 WHERE id = ?", (faded,))
        db.commit()
        run_decay(db, WriteQueue())
        rows = {r["id"]: r for r in db.fetchall("SELECT id, epistemic_status, name FROM nodes")}
    finally:
        db.close()
    assert rows[faded]["epistemic_status"] == "speculative" and rows[faded]["name"] == "faded"
    assert rows[fresh]["epistemic_status"] == "user_stated"
