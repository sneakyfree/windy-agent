"""Strand-to-green Wave 4: Soul Passport import yellows.

* C2.6 approved import writes safe items at confidence 0.5 with
       source ``imported_<platform>``; sensitive items (belief / identity)
       at confidence 0.3 with ``needs_review`` in metadata.
* C2.2 the Hermes SQLite path: a small temp SQLite DB in the table shapes
       ``hermes._extract_from_db`` looks for (``memories`` / ``facts`` /
       ``knowledge`` / ``notes`` and ``sessions`` / ``conversations`` /
       ``messages``) is detected, parsed and imported.
"""

from __future__ import annotations

import json
import sqlite3

from windyfly.memory.database import Database
from windyfly.soul_import.hermes import parse_hermes
from windyfly.soul_import.orchestrator import detect_source_type, import_soul


def _nodes(db: Database) -> dict[str, dict]:
    rows = db.fetchall("SELECT * FROM nodes")
    out = {}
    for r in rows:
        meta = r["metadata"]
        r = dict(r)
        r["metadata"] = json.loads(meta) if isinstance(meta, str) else (meta or {})
        out[r["name"]] = r
    return out


# ── C2.6 confidence + source + needs_review ─────────────────────────


def test_C2_6_safe_items_confidence_half_and_source_imported_platform(tmp_path):
    (tmp_path / "SOUL.md").write_text("- Be kind\n", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text(
        "- User prefers dark mode\n"
        "- User has two cats\n"
        "- User believes the moon landing was staged\n"
        "- User name is Sam\n",
        encoding="utf-8",
    )
    db = Database(":memory:")
    result = import_soul(db, str(tmp_path), source_type="openclaw", user_approved=True)

    assert result["imported"] == 2
    assert result["flagged"] == 2
    nodes = _nodes(db)

    for safe_name in ("User prefers dark mode", "User has two cats"):
        n = nodes[safe_name]
        assert n["confidence"] == 0.5
        assert n["source"] == "imported_openclaw"
        assert n["metadata"]["import_source"] == "imported_openclaw"
        assert "needs_review" not in n["metadata"]

    for sensitive_name in ("User believes the moon landing was staged", "User name is Sam"):
        n = nodes[sensitive_name]
        assert n["confidence"] == 0.3
        assert n["source"] == "imported_openclaw"
        assert n["metadata"]["needs_review"] is True
        assert n["epistemic_status"] == "speculative"
    db.close()


def test_C2_6_chatgpt_identity_facts_are_flagged_sensitive(tmp_path):
    """Same contract through a second platform: the label follows the
    source (imported_chatgpt) and identity statements are held for review."""
    conversations = [{
        "title": "Gardening",
        "mapping": {
            "a": {"message": {"author": {"role": "user"},
                              "content": {"parts": ["My name is Rosa. How deep do I plant garlic?"]}}},
            "b": {"message": {"author": {"role": "assistant"},
                              "content": {"parts": ["About two inches deep."]}}},
        },
    }]
    (tmp_path / "conversations.json").write_text(json.dumps(conversations), encoding="utf-8")
    db = Database(":memory:")
    assert detect_source_type(str(tmp_path)) == "chatgpt"
    result = import_soul(db, str(tmp_path), user_approved=True)
    assert result["source_type"] == "chatgpt"

    nodes = list(_nodes(db).values())
    assert nodes, "chatgpt import wrote nothing"
    assert all(n["source"] == "imported_chatgpt" for n in nodes)
    flagged = [n for n in nodes if n["metadata"].get("needs_review")]
    safe = [n for n in nodes if not n["metadata"].get("needs_review")]
    assert flagged and all(n["confidence"] == 0.3 for n in flagged)
    assert any("Rosa" in n["name"] for n in flagged)
    assert all(n["confidence"] == 0.5 for n in safe)
    assert result["flagged"] == len(flagged)
    db.close()


def test_C2_6_unapproved_import_writes_nothing(tmp_path):
    (tmp_path / "MEMORY.md").write_text("- User prefers tea\n", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("- Be kind\n", encoding="utf-8")
    db = Database(":memory:")
    result = import_soul(db, str(tmp_path), source_type="openclaw", user_approved=False)
    assert result["imported"] == 0
    assert "50% confidence" in result["preview"]
    assert db.fetchall("SELECT * FROM nodes") == []
    db.close()


# ── C2.2 Hermes SQLite import path ──────────────────────────────────


def _make_hermes_db(path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
        conn.executemany("INSERT INTO memories (content) VALUES (?)",
                         [("User drinks oat milk",), ("User has a dog named Pip",)])
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT)")
        conn.execute("INSERT INTO notes (text) VALUES ('User is learning Portuguese')")
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT)")
        conn.executemany("INSERT INTO messages (role, content) VALUES (?, ?)", [
            ("user", "Can you help me plan a trip to Lisbon?"),
            ("assistant", "Of course, when are you going?"),
            ("user", "Sometime in May."),
        ])
        conn.commit()
    finally:
        conn.close()


def test_C2_2_hermes_sqlite_parse(tmp_path):
    _make_hermes_db(tmp_path / "hermes_state.db")
    parsed = parse_hermes(str(tmp_path))

    by_content = {m["content"]: m for m in parsed["memories"]}
    for fact in ("User drinks oat milk", "User has a dog named Pip",
                 "User is learning Portuguese"):
        assert by_content[fact]["type"] == "fact"
        assert by_content[fact]["confidence"] == 0.5
        assert by_content[fact]["source"] == "imported_hermes"
    # Only the USER side of the conversation is kept.
    assert by_content["Can you help me plan a trip to Lisbon?"]["type"] == "conversation"
    assert by_content["Sometime in May."]["confidence"] == 0.3
    assert "Of course, when are you going?" not in by_content
    assert len(parsed["memories"]) == 5


def test_C2_2_hermes_sqlite_detected_and_imported(tmp_path):
    _make_hermes_db(tmp_path / "hermes_state.db")
    (tmp_path / "MEMORY.md").write_text("- User likes jazz\n", encoding="utf-8")

    assert detect_source_type(str(tmp_path)) == "hermes"
    db = Database(":memory:")
    result = import_soul(db, str(tmp_path), user_approved=True)

    assert result["source_type"] == "hermes"
    assert result["imported"] == 6  # 3 db facts + 2 user turns + 1 MEMORY.md line
    nodes = _nodes(db)
    for name in ("User drinks oat milk", "User is learning Portuguese",
                 "Sometime in May.", "User likes jazz"):
        assert nodes[name]["source"] == "imported_hermes"
        assert nodes[name]["confidence"] == 0.5
    assert nodes["Sometime in May."]["type"] == "conversation"
    db.close()


def test_C2_2_hermes_corrupt_db_does_not_break_import(tmp_path):
    (tmp_path / "broken.db").write_bytes(b"this is not a sqlite file at all" * 4)
    (tmp_path / "MEMORY.md").write_text("- User likes jazz\n", encoding="utf-8")
    parsed = parse_hermes(str(tmp_path))
    assert [m["content"] for m in parsed["memories"]] == ["User likes jazz"]
