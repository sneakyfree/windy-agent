"""Tests for search_nodes — the multi-keyword splitting fix.

Pre-fix bug: search_nodes treated the query as a single contiguous
substring, so ``_extract_keywords("what do you know about Polly?")``
yielded ``"know polly"`` and ``LIKE '%know polly%'`` returned zero
hits — the seeded Polly nodes were invisible to the parent agent's
prompt assembly.

Fix: split the query into terms, OR each LIKE clause. Tier 2 falls
back to metadata search only when name search returns nothing,
preventing unrelated-keyword prompt bloat.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from windyfly.memory.database import Database
from windyfly.memory.nodes import search_nodes


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as td:
        d = Database(str(Path(td) / "t.db"))
        try:
            yield d
        finally:
            d.close()


def _seed(db: Database, type: str, name: str, body: str = "") -> None:
    metadata = json.dumps({"description": "", "body": body})
    import uuid
    db.execute(
        "INSERT INTO nodes (id, type, name, metadata, scope_id) VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, type, name, metadata, "personal"),
    )
    db.commit()


def test_split_query_finds_node_when_one_term_matches(db):
    """The bug: 'know polly' as one substring → 0 hits.
    Fix: split → name LIKE %know% OR name LIKE %polly% → finds Polly."""
    _seed(db, "memory.project", "Polly Clone Blueprint")
    _seed(db, "memory.project", "Some Other Project")
    hits = search_nodes(db, "know polly", limit=10)
    names = {h["name"] for h in hits}
    assert "Polly Clone Blueprint" in names
    assert "Some Other Project" not in names


def test_empty_query_returns_no_hits(db):
    _seed(db, "x", "any node")
    assert search_nodes(db, "", limit=10) == []
    assert search_nodes(db, "   ", limit=10) == []


def test_name_match_takes_precedence_over_metadata(db):
    """When a node's name matches, tier 2 metadata search is skipped —
    avoids pulling unrelated nodes whose metadata happens to contain
    a common word."""
    _seed(db, "x", "Polly Specific Node")
    _seed(db, "x", "Other Node", body="this body mentions polly somewhere")
    hits = search_nodes(db, "polly", limit=10)
    # Both could match (name + body), but the tier-1 name match
    # should fire and tier-2 should be skipped.
    names = {h["name"] for h in hits}
    assert "Polly Specific Node" in names
    # The other node's body match should NOT be in tier-1 result
    # (because name-tier returned at least one hit).
    assert "Other Node" not in names


def test_metadata_fallback_when_no_name_match(db):
    """If nothing matches the name, fall back to metadata search so
    legitimate queries still find context."""
    _seed(db, "x", "Some Node", body="this body contains the rare word zorglub")
    hits = search_nodes(db, "zorglub", limit=10)
    assert len(hits) == 1
    assert hits[0]["name"] == "Some Node"


def test_multi_term_or_semantics(db):
    """Three terms — any one match in name should hit."""
    _seed(db, "x", "Cherry Pie")
    _seed(db, "x", "Banana Split")
    _seed(db, "x", "Coffee Bean")
    hits = search_nodes(db, "cherry banana coffee", limit=10)
    assert len(hits) == 3


def test_limit_honored(db):
    for i in range(20):
        _seed(db, "x", f"polly node {i}")
    hits = search_nodes(db, "polly", limit=5)
    assert len(hits) == 5


def test_unrelated_query_returns_few_or_no_hits(db):
    _seed(db, "x", "Polly Clone Blueprint")
    _seed(db, "x", "NachoCrunch Roadmap")
    # Random words that don't appear anywhere in name or metadata
    hits = search_nodes(db, "xyzabc qwerty asdfghjkl", limit=10)
    assert hits == []
