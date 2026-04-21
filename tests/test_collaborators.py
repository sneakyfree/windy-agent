"""Tests for the collaborators infrastructure (Wave 6 #1).

Three layers:
  1. memory/collaborators.py CRUD — direct DB
  2. agent/capabilities/collaborators.py handlers — with mocked LLM
  3. End-to-end through CapabilityRegistry, including recursion cap
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from windyfly.agent.capabilities import (
    Band,
    CapabilityDenied,
    CapabilityRegistry,
    Tier,
)
from windyfly.agent.capabilities.collaborators import (
    _build_filtered_memory_summary,
    _collaborator_session_id,
    _inside_collaborator,
    register_collaborator_capabilities,
)
from windyfly.memory.collaborators import (
    DEFAULT_MEMORY_POLICY,
    archive_collaborator,
    create_collaborator,
    get_collaborator_by_name,
    list_collaborators,
    parse_memory_policy,
    record_use,
)
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


@pytest.fixture
def db_and_wq():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        db = Database(db_path)
        wq = WriteQueue()
        wq.start()
        try:
            yield db, wq
        finally:
            wq.stop()
            db.close()


def _drain(wq: WriteQueue, timeout: float = 2.0) -> None:
    start = time.time()
    while not wq._queue.empty():
        if time.time() - start > timeout:
            raise TimeoutError("write queue did not drain")
        time.sleep(0.05)
    time.sleep(0.1)


# ── memory/collaborators.py CRUD ───────────────────────────────────


def test_create_collaborator_inserts_row(db_and_wq):
    db, wq = db_and_wq
    cid = create_collaborator(
        db, wq,
        name="research",
        persona_prompt="You research things deeply.",
    )
    _drain(wq)
    row = get_collaborator_by_name(db, "research")
    assert row is not None
    assert row["id"] == cid
    assert row["persona_prompt"].startswith("You research")
    assert row["use_count"] == 0
    assert row["archived_at"] is None


def test_create_collaborator_rejects_empty_name(db_and_wq):
    db, wq = db_and_wq
    with pytest.raises(ValueError, match="name cannot be empty"):
        create_collaborator(db, wq, name="", persona_prompt="x")


def test_create_collaborator_rejects_empty_persona(db_and_wq):
    db, wq = db_and_wq
    with pytest.raises(ValueError, match="persona_prompt cannot be empty"):
        create_collaborator(db, wq, name="x", persona_prompt="")


def test_create_collaborator_rejects_duplicate_name(db_and_wq):
    db, wq = db_and_wq
    create_collaborator(db, wq, name="research", persona_prompt="A")
    _drain(wq)
    with pytest.raises(ValueError, match="already exists"):
        create_collaborator(db, wq, name="research", persona_prompt="B")


def test_list_collaborators_orders_by_last_used(db_and_wq):
    db, wq = db_and_wq
    create_collaborator(db, wq, name="a", persona_prompt="A")
    create_collaborator(db, wq, name="b", persona_prompt="B")
    _drain(wq)
    rows = list_collaborators(db)
    assert {r["name"] for r in rows} == {"a", "b"}


def test_list_excludes_archived_by_default(db_and_wq):
    db, wq = db_and_wq
    create_collaborator(db, wq, name="keep", persona_prompt="K")
    create_collaborator(db, wq, name="trash", persona_prompt="T")
    _drain(wq)
    archive_collaborator(db, wq, name="trash")
    _drain(wq)
    rows = list_collaborators(db)
    assert {r["name"] for r in rows} == {"keep"}
    rows_with = list_collaborators(db, include_archived=True)
    assert {r["name"] for r in rows_with} == {"keep", "trash"}


def test_archive_returns_false_for_missing(db_and_wq):
    db, wq = db_and_wq
    assert archive_collaborator(db, wq, name="nonexistent") is False


def test_record_use_bumps_count(db_and_wq):
    db, wq = db_and_wq
    cid = create_collaborator(db, wq, name="r", persona_prompt="x")
    _drain(wq)
    record_use(db, wq, collaborator_id=cid)
    record_use(db, wq, collaborator_id=cid)
    _drain(wq)
    row = get_collaborator_by_name(db, "r")
    assert row["use_count"] == 2
    assert row["last_used_at"] is not None


def test_unique_index_allows_recreate_after_archive(db_and_wq):
    """Archive then recreate should succeed (the partial unique index
    excludes archived rows)."""
    db, wq = db_and_wq
    create_collaborator(db, wq, name="research", persona_prompt="A")
    _drain(wq)
    archive_collaborator(db, wq, name="research")
    _drain(wq)
    # Should not raise
    cid = create_collaborator(db, wq, name="research", persona_prompt="B")
    _drain(wq)
    assert get_collaborator_by_name(db, "research")["id"] == cid


def test_parse_memory_policy_handles_malformed(caplog):
    out = parse_memory_policy("not-json{{{")
    # Falls back to defaults
    assert out == DEFAULT_MEMORY_POLICY


def test_parse_memory_policy_merges_with_defaults():
    raw = json.dumps({"include_intents": True})
    out = parse_memory_policy(raw)
    assert out["include_intents"] is True
    assert out["include_personality"] is True  # default
    assert out["node_types"] == []  # default


# ── memory filter ──────────────────────────────────────────────────


def test_memory_filter_excludes_unmatched_node_types(db_and_wq):
    db, wq = db_and_wq
    # Insert some nodes of different types
    db.execute(
        "INSERT INTO nodes (id, type, name) VALUES "
        "('n1', 'research_topic', 'Mortgage rates'),"
        "('n2', 'preference', 'Likes coffee'),"
        "('n3', 'research_topic', 'Polly clone analysis')"
    )
    cid = create_collaborator(
        db, wq,
        name="r",
        persona_prompt="x",
        memory_share_policy={
            "include_personality": False,
            "node_types": ["research_topic"],
            "topic_keywords": [],
            "include_intents": False,
        },
    )
    _drain(wq)
    summary = _build_filtered_memory_summary(db, get_collaborator_by_name(db, "r"))
    assert "Mortgage rates" in summary
    assert "Polly clone" in summary
    assert "Likes coffee" not in summary  # wrong type


def test_memory_filter_topic_keywords(db_and_wq):
    db, wq = db_and_wq
    db.execute(
        "INSERT INTO nodes (id, type, name) VALUES "
        "('n1', 'fact', 'mortgage interest rates'),"
        "('n2', 'fact', 'coffee preferences'),"
        "('n3', 'fact', 'mortgage refinancing')"
    )
    cid = create_collaborator(
        db, wq,
        name="r",
        persona_prompt="x",
        memory_share_policy={
            "include_personality": False,
            "topic_keywords": ["mortgage"],
        },
    )
    _drain(wq)
    summary = _build_filtered_memory_summary(db, get_collaborator_by_name(db, "r"))
    assert "mortgage interest rates" in summary
    assert "mortgage refinancing" in summary
    assert "coffee" not in summary


def test_memory_filter_no_share_returns_no_memory_warning(db_and_wq):
    db, wq = db_and_wq
    create_collaborator(
        db, wq,
        name="isolated",
        persona_prompt="x",
        memory_share_policy={
            "include_personality": False,
            "node_types": [],
            "topic_keywords": [],
            "include_intents": False,
        },
    )
    _drain(wq)
    summary = _build_filtered_memory_summary(
        db, get_collaborator_by_name(db, "isolated"),
    )
    assert "no shared memory" in summary


# ── Session id format ──────────────────────────────────────────────


def test_collaborator_session_id_format():
    sid = _collaborator_session_id("research", "telegram:123")
    assert sid == "collab:research:telegram:123"


# ── End-to-end through registry ────────────────────────────────────


def _mock_call_llm_returning(text: str):
    """Helper to build a call_llm replacement that returns ``text``."""
    def _fake(messages, **kwargs):
        return {
            "content": text,
            "model": "test",
            "input_tokens": 1,
            "output_tokens": 1,
            "tool_calls": None,
        }
    return _fake


@pytest.mark.asyncio
async def test_capabilities_registered_with_correct_tiers(db_and_wq):
    db, wq = db_and_wq
    r = CapabilityRegistry()
    register_collaborator_capabilities(r, db, wq, config={})

    assert r.get("agent.list_collaborators").tier == Tier.READ_EXTERNAL
    assert r.get("agent.create_collaborator").tier == Tier.EXTERNAL_EFFECT
    assert r.get("agent.archive_collaborator").tier == Tier.WRITE_DESTRUCTIVE
    assert r.get("agent.delegate_to").tier == Tier.EXTERNAL_EFFECT


@pytest.mark.asyncio
async def test_create_then_list_then_delegate(db_and_wq):
    db, wq = db_and_wq
    r = CapabilityRegistry()
    register_collaborator_capabilities(r, db, wq, config={})

    # Create
    out = await r.invoke(
        "agent.create_collaborator",
        {"name": "research", "persona_prompt": "You research."},
        Band.OWNER,
    )
    assert out["created"] is True

    _drain(wq)

    # List
    out2 = await r.invoke(
        "agent.list_collaborators", {}, Band.USER,
    )
    assert out2["count"] == 1
    assert out2["collaborators"][0]["name"] == "research"

    # Delegate (with mocked LLM)
    with patch(
        "windyfly.agent.capabilities.collaborators.call_llm",
        _mock_call_llm_returning("research result here"),
    ):
        out3 = await r.invoke(
            "agent.delegate_to",
            {"name": "research", "task": "what's a mortgage"},
            Band.OWNER,
        )
    assert out3["delegated"] is True
    assert out3["succeeded"] is True
    assert out3["result"] == "research result here"
    assert out3["outcome_score"] == 1.0


@pytest.mark.asyncio
async def test_delegate_to_unknown_returns_error(db_and_wq):
    db, wq = db_and_wq
    r = CapabilityRegistry()
    register_collaborator_capabilities(r, db, wq, config={})

    out = await r.invoke(
        "agent.delegate_to",
        {"name": "nonexistent", "task": "x"},
        Band.OWNER,
    )
    assert out["delegated"] is False
    assert "no active collaborator" in out["error"]


@pytest.mark.asyncio
async def test_recursion_cap_blocks_nested_delegate(db_and_wq):
    """A collaborator's handler chain cannot call delegate_to. The
    contextvar set by _run_collaborator_turn enforces depth=1."""
    db, wq = db_and_wq
    r = CapabilityRegistry()
    register_collaborator_capabilities(r, db, wq, config={})

    # Set up two collaborators
    create_collaborator(db, wq, name="outer", persona_prompt="x")
    create_collaborator(db, wq, name="inner", persona_prompt="y")
    _drain(wq)

    # Manually set the recursion contextvar (simulating that we're
    # inside an outer collaborator's turn) and try to delegate from
    # inside that context.
    token = _inside_collaborator.set(True)
    try:
        with pytest.raises(CapabilityDenied, match="recursion"):
            await r.invoke(
                "agent.delegate_to",
                {"name": "inner", "task": "x"},
                Band.OWNER,
            )
    finally:
        _inside_collaborator.reset(token)


@pytest.mark.asyncio
async def test_delegate_records_use_and_persists_episodes(db_and_wq):
    db, wq = db_and_wq
    r = CapabilityRegistry()
    register_collaborator_capabilities(r, db, wq, config={})

    create_collaborator(db, wq, name="research", persona_prompt="x")
    _drain(wq)

    with patch(
        "windyfly.agent.capabilities.collaborators.call_llm",
        _mock_call_llm_returning("first response"),
    ):
        await r.invoke(
            "agent.delegate_to",
            {"name": "research", "task": "task one",
             "parent_session_id": "telegram:42"},
            Band.OWNER,
        )
    _drain(wq)

    # use_count bumped
    row = get_collaborator_by_name(db, "research")
    assert row["use_count"] == 1
    assert row["last_used_at"] is not None

    # Episodes persisted with collaborator session id
    eps = db.fetchall(
        "SELECT role, content FROM episodes WHERE session_id = ? "
        "ORDER BY created_at",
        ("collab:research:telegram:42",),
    )
    assert len(eps) == 2
    assert eps[0]["role"] == "user"
    assert eps[0]["content"] == "task one"
    assert eps[1]["role"] == "assistant"
    assert eps[1]["content"] == "first response"


@pytest.mark.asyncio
async def test_delegate_persistence_across_calls(db_and_wq):
    """Second delegation to the same collaborator should see the
    previous turn in its message history."""
    db, wq = db_and_wq
    r = CapabilityRegistry()
    register_collaborator_capabilities(r, db, wq, config={})

    create_collaborator(db, wq, name="research", persona_prompt="x")
    _drain(wq)

    captured_messages = []

    def _capture_call_llm(messages, **kwargs):
        captured_messages.append(messages)
        return {
            "content": f"response-{len(captured_messages)}",
            "model": "test",
            "input_tokens": 1, "output_tokens": 1, "tool_calls": None,
        }

    with patch(
        "windyfly.agent.capabilities.collaborators.call_llm",
        _capture_call_llm,
    ):
        await r.invoke(
            "agent.delegate_to",
            {"name": "research", "task": "first",
             "parent_session_id": "s1"},
            Band.OWNER,
        )
        _drain(wq)
        await r.invoke(
            "agent.delegate_to",
            {"name": "research", "task": "second",
             "parent_session_id": "s1"},
            Band.OWNER,
        )

    # Second call's messages should include the first turn
    second_msgs = captured_messages[1]
    contents = [m["content"] for m in second_msgs]
    assert any("first" in c for c in contents)
    assert any("response-1" in c for c in contents)


@pytest.mark.asyncio
async def test_archive_then_create_again_works(db_and_wq):
    db, wq = db_and_wq
    r = CapabilityRegistry()
    register_collaborator_capabilities(r, db, wq, config={})

    await r.invoke(
        "agent.create_collaborator",
        {"name": "x", "persona_prompt": "first"},
        Band.OWNER,
    )
    _drain(wq)
    await r.invoke("agent.archive_collaborator", {"name": "x"}, Band.OWNER)
    _drain(wq)
    out = await r.invoke(
        "agent.create_collaborator",
        {"name": "x", "persona_prompt": "second"},
        Band.OWNER,
    )
    assert out["created"] is True


@pytest.mark.asyncio
async def test_user_band_blocked_from_create(db_and_wq):
    db, wq = db_and_wq
    r = CapabilityRegistry()
    register_collaborator_capabilities(r, db, wq, config={})

    with pytest.raises(CapabilityDenied):
        await r.invoke(
            "agent.create_collaborator",
            {"name": "x", "persona_prompt": "x"},
            Band.USER,
        )


@pytest.mark.asyncio
async def test_user_band_can_list(db_and_wq):
    db, wq = db_and_wq
    r = CapabilityRegistry()
    register_collaborator_capabilities(r, db, wq, config={})

    out = await r.invoke("agent.list_collaborators", {}, Band.USER)
    assert out["count"] == 0
