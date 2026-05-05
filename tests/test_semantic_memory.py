"""Semantic-memory regression tests.

Pin the contract:

  - When sentence-transformers isn't installed, ``embed()`` returns
    None and ``save_episode()`` still works (embedding column NULL).
  - ``search_episodes_hybrid()`` falls back to FTS5-only when no
    embeddings are available — identical results to ``search_episodes``.
  - When embeddings ARE present (mocked here so the test runs
    everywhere), hybrid search blends FTS5 + cosine via Reciprocal
    Rank Fusion.
  - Embedding compute failure during save doesn't break the save
    (best-effort, never blocks).
  - ``cosine()`` correctness on small fixed vectors.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from windyfly.memory import embeddings as _emb
from windyfly.memory.database import Database
from windyfly.memory.episodes import (
    save_episode,
    search_episodes,
    search_episodes_hybrid,
)


@pytest.fixture
def db():
    db = Database(":memory:")
    yield db
    db.close()


# ── cosine() ───────────────────────────────────────────────────────


def test_cosine_identical_vectors():
    assert _emb.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    assert _emb.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_vectors():
    assert _emb.cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_with_none_returns_zero():
    assert _emb.cosine(None, [1.0, 0.0]) == 0.0
    assert _emb.cosine([1.0], None) == 0.0
    assert _emb.cosine(None, None) == 0.0


def test_cosine_with_mismatched_lengths_returns_zero():
    """Embeddings from different models are different lengths.
    Cosine across them is meaningless; must return 0 not crash."""
    assert _emb.cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_cosine_with_zero_vector_returns_zero():
    """Zero-magnitude division would NaN; must return 0."""
    assert _emb.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# ── Graceful fallback when sentence-transformers absent ────────────


def test_embed_returns_none_when_unavailable():
    """Without the dep, embed() must return None — never crash."""
    with patch.object(_emb, "_AVAILABLE", False):
        assert _emb.embed("hello world") is None


def test_save_episode_works_without_embeddings(db):
    """The save path must not depend on embeddings being available.
    Episode saves with embedding column NULL when unavailable."""
    with patch.object(_emb, "_AVAILABLE", False):
        eid = save_episode(db, "user", "Hi there", session_id="s1")
    rows = db.fetchall("SELECT id, embedding, embedding_model FROM episodes")
    assert len(rows) == 1
    assert rows[0]["id"] == eid
    assert rows[0]["embedding"] is None
    assert rows[0]["embedding_model"] is None


def test_save_episode_swallows_embed_failures(db):
    """If embed() crashes mid-save (model load fails, OOM, etc.),
    the save must complete with embedding=NULL — never block."""
    with patch.object(_emb, "is_available", return_value=True), \
         patch.object(_emb, "embed", side_effect=RuntimeError("boom")):
        eid = save_episode(db, "user", "Hi", session_id="s1")
    rows = db.fetchall("SELECT id, content, embedding FROM episodes WHERE id = ?", (eid,))
    assert len(rows) == 1
    assert rows[0]["content"] == "Hi"
    assert rows[0]["embedding"] is None


# ── Hybrid search degrades to FTS5 ─────────────────────────────────


def test_hybrid_falls_back_to_fts5_when_no_embeddings(db):
    """No embeddings stored → hybrid returns same results as plain FTS5.

    Patches _AVAILABLE=False so this test runs the same way on dev
    machines (no sentence-transformers) and CI (which installs the
    [semantic] extras via `uv sync --all-extras`). Without the patch,
    CI saves real embeddings, hybrid blends them, and the result
    diverges from the pure-FTS5 fallback this test is verifying."""
    with patch.object(_emb, "_AVAILABLE", False):
        save_episode(db, "user", "I have a dog named Atlas", session_id="s1")
        save_episode(db, "user", "I love coffee in the morning", session_id="s1")
        save_episode(db, "user", "My favorite number is 42", session_id="s1")

        fts = search_episodes(db, "Atlas dog")
        hybrid = search_episodes_hybrid(db, "Atlas dog")
    fts_ids = {r["id"] for r in fts}
    hybrid_ids = {r["id"] for r in hybrid}
    assert fts_ids == hybrid_ids


def test_hybrid_session_filter(db):
    """session_id filter must work in hybrid even without embeddings."""
    with patch.object(_emb, "_AVAILABLE", False):
        save_episode(db, "user", "Atlas in session A", session_id="A")
        save_episode(db, "user", "Atlas in session B", session_id="B")
        out = search_episodes_hybrid(db, "Atlas", session_id="A")
    assert len(out) == 1
    assert out[0]["session_id"] == "A"


def test_hybrid_exclude_ids(db):
    """exclude_ids parameter must filter both FTS5 and semantic paths."""
    with patch.object(_emb, "_AVAILABLE", False):
        e1 = save_episode(db, "user", "Atlas the dog", session_id="s1")
        e2 = save_episode(db, "user", "Atlas the very good dog", session_id="s1")
        out = search_episodes_hybrid(db, "Atlas", exclude_ids={e1})
    assert len(out) == 1
    assert out[0]["id"] == e2


def test_hybrid_limit_respected(db):
    """Hybrid must cap at limit even when both paths return many hits."""
    with patch.object(_emb, "_AVAILABLE", False):
        for i in range(20):
            save_episode(db, "user", f"Atlas adventure number {i}", session_id="s1")
        out = search_episodes_hybrid(db, "Atlas adventure", limit=5)
    assert len(out) == 5


# ── Hybrid with mocked embeddings ──────────────────────────────────


def _mock_embed_factory():
    """Returns an embed() mock that produces predictable vectors based
    on which keywords appear in the input. Lets us test RRF behavior
    without installing sentence-transformers OR numpy — uses stdlib
    struct.pack to produce the BLOB shape embed() would."""
    import struct

    def mock_embed(text: str):
        # 4-dim semantic-ish space:
        #   dim 0: pet/dog/atlas
        #   dim 1: coffee
        #   dim 2: number/42
        #   dim 3: weather/sunny
        low = (text or "").lower()
        v = [
            1.0 if any(w in low for w in ("pet", "dog", "atlas", "puppy")) else 0.0,
            1.0 if any(w in low for w in ("coffee", "espresso", "latte")) else 0.0,
            1.0 if any(w in low for w in ("number", "42", "favorite")) else 0.0,
            1.0 if any(w in low for w in ("weather", "sunny", "rain")) else 0.0,
        ]
        if sum(v) == 0:
            return None
        return struct.pack("4f", *v)

    return mock_embed


def test_hybrid_finds_paraphrase_via_semantic(db):
    """The actual differentiator: a keyword search for 'pet' wouldn't
    find 'Atlas the dog' (only 'pet' isn't there), but semantic
    space groups them. Verify hybrid returns the dog episode for a
    'pet' query when embeddings are present.

    This is the story PR #127 sells — paraphrase recall."""
    mock_embed = _mock_embed_factory()
    with patch.object(_emb, "is_available", return_value=True), \
         patch.object(_emb, "embed", side_effect=mock_embed), \
         patch.object(_emb, "model_name", return_value="mock-test"):
        save_episode(db, "user", "Atlas the dog is 4 years old", session_id="s1")
        save_episode(db, "user", "I drink coffee every morning", session_id="s1")
        save_episode(db, "user", "My favorite number is 42", session_id="s1")

        # Verify embeddings actually got stored
        with_emb = db.fetchall(
            "SELECT id, content, embedding FROM episodes WHERE embedding IS NOT NULL"
        )
        assert len(with_emb) == 3, "all 3 episodes should have stored embeddings"

        # Paraphrase query: "pet" never appears in any saved content,
        # but it lights up the same dim as "dog/atlas". FTS5 alone
        # won't find anything; hybrid+semantic should surface the dog.
        out = search_episodes_hybrid(db, "pet")
    contents = [r["content"] for r in out]
    assert any("Atlas" in c for c in contents), (
        f"semantic search must surface dog episode for 'pet' query; got {contents}"
    )


def test_hybrid_blends_fts5_and_semantic_via_rrf(db):
    """FTS5 prefers exact keyword match; semantic prefers concept
    overlap. RRF should surface BOTH when a query has both axes."""
    mock_embed = _mock_embed_factory()
    with patch.object(_emb, "is_available", return_value=True), \
         patch.object(_emb, "embed", side_effect=mock_embed), \
         patch.object(_emb, "model_name", return_value="mock-test"):
        # Episode A: matches FTS5 (exact "Atlas") but not the
        # semantic concept (no pet vocab in this contrived example —
        # but our mock embedder gives it pet-dim because of "atlas")
        save_episode(db, "user", "Atlas was a Greek titan", session_id="s1")
        # Episode B: matches semantic (dog/pet) but not "Atlas" by
        # exact keyword
        save_episode(db, "user", "I love my puppy so much", session_id="s1")

        out = search_episodes_hybrid(db, "Atlas pet")
    contents = [r["content"] for r in out]
    # Both should be in the top results — Atlas (FTS5 hit) AND
    # puppy (semantic hit on "pet" → dog dim)
    assert any("Atlas" in c for c in contents)
    assert any("puppy" in c for c in contents)


def test_semantic_weight_zero_disables_semantic(db):
    """Setting semantic_weight=0 must skip the semantic path entirely,
    even when embeddings are available. Operator escape hatch."""
    mock_embed = _mock_embed_factory()
    with patch.object(_emb, "is_available", return_value=True), \
         patch.object(_emb, "embed", side_effect=mock_embed), \
         patch.object(_emb, "model_name", return_value="mock-test"):
        save_episode(db, "user", "puppy time", session_id="s1")

        # With semantic_weight=0, "pet" query falls back to FTS5 only
        # → no match (puppy ≠ pet by keyword)
        out = search_episodes_hybrid(db, "pet", semantic_weight=0.0)
    assert out == []


# ── Deserialize round-trip ─────────────────────────────────────────


def test_blob_round_trip():
    """A vector → bytes → vector round trip must preserve values.
    Uses stdlib struct so the test runs on every install, not only
    the semantic-extras one."""
    import struct
    original = [0.1, -0.5, 0.999, 0.0]
    blob = struct.pack("4f", *original)
    decoded = _emb.deserialize(blob)
    assert decoded is not None
    assert len(decoded) == 4
    for a, b in zip(original, decoded):
        assert abs(a - b) < 1e-6


def test_deserialize_none_returns_none():
    assert _emb.deserialize(None) is None
    assert _emb.deserialize(b"") is None
