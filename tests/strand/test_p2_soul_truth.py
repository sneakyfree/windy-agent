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


KEEP, DROP = "zqxkeep1111", "zqxdrop2222"


def _flagged_import(db, tmp_path, monkeypatch):
    """Approve an import whose two items are both sensitive, so both wait for review."""
    import asyncio

    from windyfly.channels.base import handle_incoming
    from windyfly.commands.core import wire_runtime
    from windyfly.commands.setup import init_all_commands
    from windyfly.soul_import import flow

    monkeypatch.setitem(orchestrator._PARSERS, "openclaw", lambda path: {
        "personality": {}, "soul": "", "skills": [],
        "memories": [{"type": "identity", "content": f"my locker code is {KEEP}"},
                     {"type": "identity", "content": f"my bank pin is {DROP}"}]})
    init_all_commands(db=db, config={})
    wire_runtime(db=db)
    flow._review_lists.clear()
    assert import_soul(db, str(tmp_path), source_type="openclaw", user_approved=True)["flagged"] == 2

    def say(text):
        ok, out = asyncio.run(handle_incoming(text, {"platform": "cli", "channel_id": "p2-21-review"}))
        assert ok
        return out
    return say


def _prompt_text(db, query):
    messages = assemble_prompt({"personality": {}}, db, query, "s-review")
    return "\n".join(str(m.get("content") or "") for m in messages)


def test_p2_21_the_owner_reviews_flagged_items_one_at_a_time_and_keeps_one(tmp_path, monkeypatch):
    from windyfly.memory.nodes import search_nodes

    db = Database(":memory:")
    try:
        say = _flagged_import(db, tmp_path, monkeypatch)
        listed = say("/soul review")
        assert "2 imported items" in listed and "1. identity" in listed and "2. identity" in listed
        assert KEEP not in listed and DROP not in listed           # the list shows kind only, never the text
        assert KEEP in say("/soul review 1")                       # one item opened on purpose
        assert KEEP not in _prompt_text(db, "what is my locker code")  # still inert before keep
        assert "Kept 1 item" in say("/soul keep 1")
        assert KEEP in _prompt_text(db, "what is my locker code")      # kept: now the agent can use it
        assert not search_nodes(db, DROP)                          # the other one still waits
        assert "1 imported item wait" in say("/soul review")
    finally:
        db.close()


def test_p4_c2_the_owner_drops_flagged_items_after_a_yes_leaving_nothing_readable(tmp_path, monkeypatch):
    path = tmp_path / "memory.db"
    export = tmp_path / "export"
    export.mkdir()
    db = Database(str(path))

    def raw():
        return b"".join(p.read_bytes() for p in (path, Path(f"{path}-wal")) if p.exists())
    try:
        say = _flagged_import(db, export, monkeypatch)
        db.commit()
        assert DROP.encode() in raw()
        say("/soul review")
        asked = say("/soul drop all")
        assert "Delete 2 items for good?" in asked and "/soul yes" in asked
        assert DROP not in asked and DROP.encode() in raw()        # asked by kind and date; nothing deleted yet
        assert "Deleted 2 items" in say("/soul yes")
        assert DROP.encode() not in raw() and KEEP.encode() not in raw()
        assert "Nothing is waiting" in say("/soul review")
    finally:
        db.close()
    assert DROP.encode() not in raw()



def _chatgpt_export(tmp_path, owner: list[str], assistant: list[str]):
    import json

    mapping = {f"u{i}": {"message": {"author": {"role": "user"}, "content": {"parts": [t]}}} for i, t in enumerate(owner)}
    mapping.update({f"a{i}": {"message": {"author": {"role": "assistant"}, "content": {"parts": [t]}}}
                    for i, t in enumerate(assistant)})
    (tmp_path / "conversations.json").write_text(json.dumps([{"title": "chat", "mapping": mapping}]))
    return str(tmp_path)


def test_p2_21_chatgpt_style_comes_from_the_owners_own_messages_only(tmp_path):
    from windyfly.soul_import.chatgpt import parse_chatgpt

    casual = ["lol that's wild", "ok", "gonna try it haha", "yeah idk", "can't wait lol", "sure"] * 4
    formal_reply = ["Thank you kindly for your question. I would be pleased to help you with a full and careful answer."] * 30
    style = parse_chatgpt(_chatgpt_export(tmp_path, casual, formal_reply))["personality"]
    assert style["humor"] >= 8 and style["formality"] <= 3 and style["verbosity"] == 2
    # the assistant's side changes nothing: the same owner messages alone give the same numbers
    alone = tmp_path / "alone"
    alone.mkdir()
    assert parse_chatgpt(_chatgpt_export(alone, casual, []))["personality"] == style

    polite = ["Could you please summarise the attached report for the quarterly board meeting, thank you."] * 24
    other = tmp_path / "polite"
    other.mkdir()
    style = parse_chatgpt(_chatgpt_export(other, polite, []))["personality"]
    assert style == {"humor": 3, "formality": 10, "verbosity": 5}
    few = tmp_path / "few"
    few.mkdir()
    assert parse_chatgpt(_chatgpt_export(few, casual[:5], []))["personality"] == {}     # too little to judge


def test_p2_21_an_imported_tone_is_applied_after_a_yes_and_can_be_undone(tmp_path, monkeypatch):
    import asyncio
    import io
    import zipfile

    from windyfly.channels.base import handle_incoming
    from windyfly.commands.core import wire_runtime
    from windyfly.commands.setup import init_all_commands
    from windyfly.control_panel import get_sliders
    from windyfly.personality.versioning import detect_drift
    from windyfly.soul_import import flow
    from windyfly.soul_import import intake as ik

    monkeypatch.setattr(ik, "_base_dir", lambda: tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SOUL.md", "- Warm\n")
        z.writestr("config.yaml", "humor: 8\nformality: 2\n")
    db = Database(":memory:")
    try:
        init_all_commands(db=db, config={})
        wire_runtime(db=db)
        for held in (flow._pending, flow._confirm, flow._tones):
            held.clear()

        def say(text):
            ok, out = asyncio.run(handle_incoming(text, {"platform": "cli", "channel_id": "p2-21-tone"}))
            assert ok
            return out

        def tone():
            return {k: int(float(v)) for k, v in get_sliders(db).items() if k in ("humor", "formality")}

        say("/slider humor 5")                                      # a history to measure drift against
        say("/slider formality 5")
        say("/soul import")
        flow.take_file(db, "cli:p2-21-tone", io.BytesIO(buf.getvalue()), "export.zip", max_bytes=10**6)
        assert "formality 2, humor 8" in say("/soul approve")
        asked = say("/soul apply")
        assert "humor: 5 -> 8" in asked and "formality: 5 -> 2" in asked and "Raw mode is on" in asked
        assert tone() == {"humor": 5, "formality": 5}              # nothing changes before the yes
        assert "Applied the imported tone: formality 2, humor 8" in say("/soul yes")
        assert tone() == {"humor": 8, "formality": 2}
        assert detect_drift(db) is None                            # moved 3 points, but on the owner's own yes
        assert "Put back formality 5, humor 5" in say("/soul undo")
        assert tone() == {"humor": 5, "formality": 5}

        say("/soul apply")
        say("/soul yes")
        say("/slider humor 6")                                      # the owner changes one afterwards
        undone = say("/soul undo")
        assert "Put back formality 5" in undone and "Left humor" in undone
        assert tone() == {"humor": 6, "formality": 5}
        assert "no imported tone to undo" in say("/soul undo")
    finally:
        db.close()


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
                 "Actually you're thinking of my sister.",
                 "That was wrong, I asked for Python not Go.",
                 "Actually, the deadline is next week, not tomorrow."):
        got = detect_friction(text)
        assert got and got["fault_type"] == "factual_error", text
    for text in ("Actually, that's exactly right!",
                 "Actually, you're totally right about that.",
                 "Actually it's perfect, thank you.",
                 "That's not wrong at all, nice work.",
                 "Is that wrong? I can't tell.",
                 "No problem, that works great!",
                 # Hub's offline run on #580: praise, self-blame and banter are not corrections.
                 "Actually, it's a great idea, let's do it",
                 "Actually, it's exactly what I needed",
                 "Actually, that's what I wanted",
                 "Actually, the food was good",
                 "Actually, the answer is perfect",
                 "Actually, you are so helpful",
                 "That was wrong of me, sorry",
                 "I thought that was wrong but it's fine",
                 "Wrong, wrong, wrong, haha you got it right",
                 "Actually, it's my fault, not yours",
                 "That was not right of me, apologies"):
        assert detect_friction(text) is None, text
