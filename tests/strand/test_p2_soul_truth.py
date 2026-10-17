"""Strand v2, Part 2 genes 2.1-2.4: soul import, cockpit, truth layer, "Never Wrong Twice"
(docs/strand/STRAND_V2.md). Temp files and a stub model; no network, no home dir.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from windyfly.agent.loop import agent_respond
from windyfly.agent.prompt import assemble_prompt
from windyfly.memory.database import Database
from windyfly.memory.nodes import upsert_node
from windyfly.memory.write_queue import WriteQueue
from windyfly.soul_import import orchestrator
from windyfly.soul_import.orchestrator import import_soul
from windyfly.tools.registry import ToolRegistry


def _export(tmp_path):
    (tmp_path / "SOUL.md").write_text("- Be kind\n")
    (tmp_path / "MEMORY.md").write_text("- User likes oolong tea\n- User prefers dark mode\n")
    return str(tmp_path)


def _count(db, table):
    return db.fetchone(f"SELECT COUNT(*) AS c FROM {table}")["c"]


# ── gene 2.1 soul import ────────────────────────────────────────────────────────────────

def test_p2_21_preview_writes_nothing(tmp_path):
    db = Database(":memory:")
    try:
        before = (_count(db, "nodes"), _count(db, "skills"))
        result = import_soul(db, _export(tmp_path), source_type="openclaw", user_approved=False)
        assert result["preview"] and result["imported"] == 0
        assert (_count(db, "nodes"), _count(db, "skills")) == before
    finally:
        db.close()


def test_p2_21_approved_items_are_stored_as_low_confidence_imports(tmp_path):
    db = Database(":memory:")
    try:
        import_soul(db, _export(tmp_path), source_type="openclaw", user_approved=True)
        rows = db.fetchall("SELECT name, confidence, source, epistemic_status FROM nodes "
                           "WHERE name LIKE '%oolong%' OR name LIKE '%dark mode%'")
    finally:
        db.close()
    assert len(rows) == 2
    assert {(r["confidence"], r["source"], r["epistemic_status"]) for r in rows} == {(0.5, "imported_openclaw", "inferred")}


def test_p2_21_sensitive_items_are_flagged_and_code_skills_skipped(tmp_path, monkeypatch):
    parsed = {
        "personality": {}, "soul": "",
        "memories": [{"type": "identity", "content": "I am a vegetarian"}],
        "skills": [{"name": "notes", "code": "# How I take notes", "language": "markdown"},
                   {"name": "rm", "code": "import os; os.system('rm -rf /')", "language": "python"}],
    }
    monkeypatch.setitem(orchestrator._PARSERS, "openclaw", lambda path: parsed)
    db = Database(":memory:")
    try:
        result = import_soul(db, str(tmp_path), source_type="openclaw", user_approved=True)
        node = db.fetchone("SELECT confidence, epistemic_status, metadata FROM nodes WHERE name LIKE '%vegetarian%'")
        skills = db.fetchall("SELECT name, language, promoted FROM skills")
    finally:
        db.close()
    assert result["flagged"] == 1 and result["skipped"] == 1
    assert node["confidence"] == 0.3 and node["epistemic_status"] == "speculative" and "needs_review" in node["metadata"]
    assert [(s["name"], s["language"], bool(s["promoted"])) for s in skills] == [("notes", "playbook", False)]


# ── gene 2.3 truth layer ────────────────────────────────────────────────────────────────

def _knowledge(strictness: int) -> str:
    db = Database(":memory:")
    try:
        for status in ("verified", "user_stated", "inferred", "speculative", "observed"):
            upsert_node(db, "fact", f"kettle_{status}", epistemic_status=status)
        config = {"agent": {}, "memory": {}, "personality": {"preset": "buddy", "epistemic_strictness": strictness}}
        messages = assemble_prompt(config, db, "where is the kettle", "s1")
    finally:
        db.close()
    blocks = [m["content"] for m in messages if "Relevant Knowledge" in m.get("content", "")]
    assert len(blocks) == 1, "the knowledge block must be there for this test to mean anything"
    return blocks[0]


def test_p2_23_strictness_above_7_drops_inferred_and_speculative():
    block = _knowledge(8)
    assert "[VERIFIED]" in block and "[USER_STATED]" in block
    assert "[INFERRED]" not in block and "[SPECULATIVE]" not in block
    assert "[OBSERVED]" in block          # above 7 only the two weakest go


def test_p2_23_strictness_above_9_keeps_only_verified_and_user_stated():
    block = _knowledge(10)
    assert "kettle_verified" in block and "kettle_user_stated" in block
    assert "kettle_inferred" not in block and "kettle_speculative" not in block
    assert "kettle_observed" not in block   # above 9 nothing but verified and user_stated


def test_p2_23_every_fact_carries_its_label_at_low_strictness():
    block = _knowledge(3)
    for label in ("[VERIFIED]", "[USER_STATED]", "[INFERRED]", "[SPECULATIVE]"):
        assert label in block


def test_p2_23_the_model_is_told_to_say_how_sure_it_is():
    db = Database(":memory:")
    try:
        messages = assemble_prompt({"agent": {}, "memory": {}, "personality": {"preset": "buddy"}}, db, "hi", "s1")
    finally:
        db.close()
    assert "indicate your confidence level" in messages[0]["content"]
    assert "If a fact is marked INFERRED, say so." in messages[0]["content"]


# ── gene 2.4 "Never Wrong Twice" ────────────────────────────────────────────────────────

def test_p2_24_a_correction_reaches_the_model_as_a_correction():
    seen: list[str] = []

    def llm(messages, *a, **k):
        seen.extend(m.get("content") or "" for m in messages)
        return {"content": "Sorry, Sam.", "tool_calls": None, "input_tokens": 5, "output_tokens": 5}

    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "c.db"))
        wq = WriteQueue()
        wq.start()
        try:
            with patch("windyfly.agent.loop.call_llm", side_effect=llm):
                agent_respond({"agent": {}, "memory": {}, "personality": {"preset": "buddy"}}, db, wq,
                              "No, that's wrong, my name is Sam", "s1", ToolRegistry())
        finally:
            wq.stop()
            db.close()
    assert any("The user just corrected you" in c for c in seen)


def test_p2_24_common_corrections_are_detected():
    from windyfly.agent.failure_detector import detect_friction

    for text in ("That is wrong, the meeting is on Friday.",
                 "That's not right, I live in Denver.",
                 "Incorrect, it was 1987 not 1989.",
                 "Wrong, the file is in /tmp.",
                 "Actually, it's Tuesday today.",
                 "Actually, the train leaves at 6.",
                 "Actually you're thinking of my sister."):
        got = detect_friction(text)
        assert got and got["fault_type"] == "factual_error", text
    for text in ("Actually, that's exactly right!",
                 "Actually, you're totally right about that.",
                 "Actually it's perfect, thank you.",
                 "That's not wrong at all, nice work.",
                 "Is that wrong? I can't tell.",
                 "No problem, that works great!"):
        assert detect_friction(text) is None, text
