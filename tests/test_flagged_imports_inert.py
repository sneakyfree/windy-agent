"""An imported item flagged for review (needs_review) stays out of the prompt and every recall until kept."""

from __future__ import annotations

from windyfly.agent.prompt import assemble_prompt
from windyfly.memory.database import Database
from windyfly.memory.nodes import get_all_nodes, get_nodes_by_type, search_nodes, upsert_node

FLAGGED = "zqxflagged5512 my bank pin is zqxsecretpin9911"
SAFE = "zqxsafe5512 likes sourdough"


def _db():
    db = Database(":memory:")
    upsert_node(db, type="fact", name=FLAGGED, confidence=0.3, epistemic_status="speculative",
                source="imported_openclaw",
                metadata={"raw_content": FLAGGED, "import_source": "imported_openclaw", "needs_review": True})
    upsert_node(db, type="fact", name=SAFE, confidence=0.5, source="imported_openclaw",
                metadata={"raw_content": SAFE, "import_source": "imported_openclaw"})
    db.commit()
    return db


def test_a_flagged_import_is_not_in_the_assembled_prompt():
    db = _db()
    try:
        msgs = assemble_prompt({"personality": {}}, db, "what do you know about zqxflagged5512 and zqxsafe5512",
                               "s-flag")
    finally:
        db.close()
    text = "\n".join(str(m.get("content") or "") for m in msgs)
    assert "likes sourdough" in text                      # the kept import is recalled
    assert "zqxsecretpin9911" not in text                 # the flagged one is not (the user named both)


def test_no_listing_or_search_returns_a_flagged_import():
    db = _db()
    try:
        assert [n["name"] for n in search_nodes(db, "zqxflagged5512 zqxsafe5512")] == [SAFE]
        assert [n["name"] for n in get_nodes_by_type(db, "fact")] == [SAFE]
        assert [n["name"] for n in get_all_nodes(db)] == [SAFE]
    finally:
        db.close()


def test_a_row_with_non_json_metadata_never_breaks_a_query():
    db = Database(":memory:")
    try:
        db.execute("INSERT INTO nodes (id, type, name, metadata) VALUES ('x', 'fact', 'plain zqxraw', 'not json')")
        db.commit()
        assert [n["name"] for n in search_nodes(db, "zqxraw")] == ["plain zqxraw"]
    finally:
        db.close()
