"""Regression: /remember must actually persist a fact.

Surfaced 2026-07-06 over Windy Chat: cmd_remember returned a bare
"REMEMBER:<fact>" sentinel that no layer ever intercepted, so the fact was
echoed to the user and never saved — /facts stayed empty and recall found
nothing. It now writes an asserted user fact via upsert_node.
"""

from __future__ import annotations

import pytest

from windyfly.channels.base import handle_incoming
from windyfly.commands.setup import init_all_commands
from windyfly.memory.database import Database
from windyfly.memory.nodes import get_all_nodes


@pytest.fixture
def booted_db():
    from windyfly.commands.core import wire_runtime
    db = Database(":memory:")
    init_all_commands(db=db, config={})
    wire_runtime(db=db)
    yield db
    db.close()


@pytest.mark.asyncio
async def test_remember_writes_a_fact_node(booted_db):
    ok, out = await handle_incoming(
        "/remember my dog is named Biscuit",
        {"platform": "matrix", "channel_id": "x"},
    )
    assert ok is True
    assert "REMEMBER:" not in out  # no raw sentinel leaked to the user
    assert "Biscuit" in out

    facts = get_all_nodes(booted_db, node_type="fact")
    assert any("Biscuit" in (n.get("name") or "") for n in facts), (
        "the fact was not persisted to the nodes store"
    )


@pytest.mark.asyncio
async def test_remember_then_facts_shows_it(booted_db):
    await handle_incoming(
        "/remember I take my coffee black",
        {"platform": "matrix", "channel_id": "x"},
    )
    ok, out = await handle_incoming("/facts", {"platform": "matrix", "channel_id": "x"})
    assert "coffee black" in out


@pytest.mark.asyncio
async def test_remember_empty_shows_usage(booted_db):
    ok, out = await handle_incoming("/remember", {"platform": "matrix", "channel_id": "x"})
    assert "Usage" in out
