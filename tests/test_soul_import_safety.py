"""Soul import safety (Hub's review of the design, and his note on #583)."""

from __future__ import annotations

import json

from windyfly.agent.prompt import assemble_prompt
from windyfly.memory.database import Database
from windyfly.memory.nodes import search_nodes, upsert_node
from windyfly.soul_import import orchestrator

SECRET = "zqxsecret7788"


def _parsed(memories):
    return {"source": "openclaw", "personality": {}, "memories": memories, "skills": []}


def test_a_flagged_import_never_touches_an_existing_node_nor_holds_a_conflict(tmp_path, monkeypatch):
    db = Database(":memory:")
    try:
        upsert_node(db, type="identity", name="zqxname", metadata={"raw_content": "the owner's own value"},
                    source="owner_stated")
        db.commit()
        monkeypatch.setitem(orchestrator._PARSERS, "openclaw", lambda p: _parsed(
            [{"type": "identity", "content": "zqxname"},                      # same type+name as the owner's
             {"type": "identity", "content": f"zqxnew pin is {SECRET}"}]))     # a new sensitive item
        out = orchestrator.import_soul(db, str(tmp_path), "openclaw", user_approved=True)
        assert out["flagged"] == 1 and out["skipped"] >= 1
        own = db.fetchone("SELECT metadata FROM nodes WHERE type = 'identity' AND name = 'zqxname'")
        assert json.loads(own["metadata"]) == {"raw_content": "the owner's own value"}   # untouched
        assert db.fetchone("SELECT COUNT(*) AS n FROM conflicts")["n"] == 0            # no held conflict
        assert [n["name"] for n in search_nodes(db, "zqxname")] == ["zqxname"]         # still recalled
        msgs = assemble_prompt({"personality": {}}, db, "tell me about zqxname and zqxnew", "s-imp")
        text = "\n".join(str(m.get("content") or "") for m in msgs)
        assert SECRET not in text
    finally:
        db.close()


def test_imports_are_tagged_as_imported_data(tmp_path, monkeypatch):
    db = Database(":memory:")
    try:
        monkeypatch.setitem(orchestrator._PARSERS, "openclaw", lambda p: _parsed(
            [{"type": "fact", "content": "zqxfact likes tea"}, {"type": "identity", "content": "zqxid"}]))
        orchestrator.import_soul(db, str(tmp_path), "openclaw", user_approved=True)
        metas = [json.loads(r["metadata"]) for r in db.fetchall("SELECT metadata FROM nodes")]
        assert {m.get("source") for m in metas} == {"import:openclaw"}
    finally:
        db.close()


def test_a_secret_in_any_memory_type_waits_for_review(tmp_path, monkeypatch):
    """Hub on #586: a PIN typed 'fact' was imported as safe and recalled; now it is flagged."""
    db = Database(":memory:")
    try:
        monkeypatch.setitem(orchestrator._PARSERS, "openclaw", lambda p: _parsed(
            [{"type": "fact", "content": f"bank card pin is {SECRET}"},
             {"type": "preference", "content": "zqxsafe likes window seats"}]))
        out = orchestrator.import_soul(db, str(tmp_path), "openclaw", user_approved=True)
        assert out["flagged"] == 1 and out["imported"] == 1
        assert not search_nodes(db, SECRET) and search_nodes(db, "zqxsafe")
        msgs = assemble_prompt({"personality": {}}, db, "what is my bank card pin", "s-pin")
        assert SECRET not in "\n".join(str(m.get("content") or "") for m in msgs)
    finally:
        db.close()


def test_the_secret_screen_flags_secrets_and_lets_ordinary_memories_through():
    from windyfly.soul_import.preview import looks_like_secret

    for text in ("My PIN is 4421", "wifi password: hunter2", "bank account number 12345678",
                 "card 4111 1111 1111 1111", "SSN 123-45-6789", "key sk-ant-abcdefghijklmnopqrstu",
                 "seed phrase is apple banana", "IBAN GB29NWBK60161331926819", "ghp_abcdefghijklmnop1234"):
        assert looks_like_secret(text), text
    for text in ("User likes oolong tea", "Lives in Ohio since 2019", "Has two kids, 7 and 9",
                 "Spinning class on Tuesdays", "Pineapple on pizza is fine", "Born 1984-05-12"):
        assert not looks_like_secret(text), text

