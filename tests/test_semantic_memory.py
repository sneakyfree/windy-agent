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


# ── Concurrency: the segfault guard ────────────────────────────────
#
# Every test above mocks `is_available()` and `embed()`. That is why
# the crash below survived 3,444 green tests: nothing in the suite ever
# loaded a REAL model, so the concurrent native-initialization path was
# never executed. These tests only run on a semantic-extras install,
# which is exactly where the bug lived.


needs_real_model = pytest.mark.skipif(
    not _emb.is_available(),
    reason="requires the [semantic] extra (real sentence-transformers)",
)


@needs_real_model
def test_concurrent_embed_does_not_crash_the_process():
    """Two threads embedding at once must not fault the interpreter.

    This agent does exactly that on EVERY turn: the main thread embeds
    the query (prompt.assemble_prompt → episodes.search_episodes_hybrid)
    while the WriteQueue worker embeds the episode content
    (episodes.save_episode).

    Before the lock in `embeddings._load_model` / `embed`, both threads
    constructed a SentenceTransformer concurrently and the process died
    — SIGSEGV (exit 139) on some runs, an unkillable hang inside
    transformers' `_preprocess_mask_arguments` on others. Apple Silicon
    made it near-deterministic because sentence-transformers
    auto-selects the Metal backend there (`AUTO DEVICE: mps:0`).

    A native abort is NOT an exception: `embed()`'s `except Exception`
    never sees it, so the module's graceful-degradation contract
    silently does not apply and the supervisor restarts straight into
    the same crash. If this test regresses it will not fail politely —
    it will take the whole pytest process down, which is the honest
    signal for a defect of this class.
    """
    import threading

    errors: list[str] = []

    def worker(tag: str) -> None:
        try:
            for i in range(20):
                _emb.embed(f"{tag} a beagle named Biscuit, message {i}")
        except BaseException as exc:                     # noqa: BLE001
            errors.append(f"{tag}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(f"t{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)

    assert not [t for t in threads if t.is_alive()], "embed() deadlocked"
    assert errors == []


@needs_real_model
def test_cold_concurrent_load_yields_one_shared_model():
    """The FIRST embed from several threads must build exactly one model.

    Resets the module cache so the load genuinely races, then asserts
    every thread ended up on the same object. Two models meant two
    concurrent native constructions — the crash.
    """
    import threading

    with _emb._MODEL_LOCK:
        _emb._MODEL = None
        _emb._MODEL_NAME = None

    seen: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        _emb.embed("cold start racer")
        with lock:
            seen.append(id(_emb._MODEL))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)

    assert _emb._MODEL is not None
    assert len(set(seen)) == 1, f"more than one model constructed: {set(seen)}"


# ── Retrieval reach ────────────────────────────────────────────────


def test_semantic_query_overrides_query_for_the_embedding():
    """The cosine half must embed the RAW utterance, not keyword soup.

    FTS5 wants keywords; an embedding model wants the sentence it was
    trained on. Callers used to pass `_extract_keywords()` output to
    both, so "where do i keep my money" was embedded as "where keep
    money" — discarding the words that carry the meaning.
    """
    db = Database(":memory:")
    save_episode(db, "user", "i bank at Adirondack Trust", session_id="s")

    embedded: list[str] = []

    def _spy(text):
        embedded.append(text)
        return None            # None → semantic half no-ops; FTS still runs

    with patch.object(_emb, "is_available", return_value=True), \
         patch.object(_emb, "embed", side_effect=_spy):
        search_episodes_hybrid(
            db, query="where keep money",
            semantic_query="where do i keep my money",
            session_id="s",
        )

    assert embedded == ["where do i keep my money"]
    db.close()


def test_semantic_pool_reaches_past_the_verbatim_window():
    """The pool must search well past what the prompt already injects.

    prompt.assemble_prompt injects the most recent 30 episodes verbatim
    (5 + context_window*5). A 50-episode pool therefore searched barely
    20 episodes beyond what the model could already see, which made the
    semantic half almost decorative — anything genuinely old, the only
    thing retrieval is FOR, sat outside the horizon by construction.

    Marathon measurement across 45 probes at distances 5→240 turns:
    pool=50 → 7 retrieval misses; pool=2000 → 1.
    """
    import inspect
    default = inspect.signature(search_episodes_hybrid).parameters[
        "semantic_pool"
    ].default
    assert default >= 500, (
        f"semantic_pool default is {default}; anything near the "
        "30-episode verbatim window makes semantic search pointless"
    )


class TestBrokenInstallDegradesGracefully:
    """An OPTIONAL extra must never be able to crash the agent.

    Proven on the OC5 Intel iMac, 2026-07-30: installing
    sentence-transformers there yields an install that raises

        NameError: name 'torch' is not defined

    on import, because PyTorch's last x86_64 macOS wheel was 2.2.2 and
    pip resolves modern transformers against it. ``is_available()``
    guarded only ``except ImportError``, so that NameError escaped, the
    memory subsystem raised, and the supervisor would restart straight
    back into it — the OpenClaw death spiral, from an OPTIONAL
    dependency, on a machine whose only sin was being an Intel Mac.

    "Installed but broken" is worse than "absent": the user believes
    they have semantic memory. Degrade, warn once, carry on.
    """

    @staticmethod
    def _fail_import_with(monkeypatch, exc):
        """Make ``import sentence_transformers`` raise ``exc``.

        Uses a meta-path finder rather than patching ``builtins.__import__``
        — patching that recurses once pytest's own machinery imports
        through the replacement during teardown.
        """
        import sys

        class ExplodingFinder:
            def find_spec(self, name, path=None, target=None):
                if name == "sentence_transformers":
                    raise exc
                return None

        monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
        monkeypatch.setattr(sys, "meta_path", [ExplodingFinder(), *sys.meta_path])
        from windyfly.memory import embeddings
        monkeypatch.setattr(embeddings, "_AVAILABLE", None)
        return embeddings

    def test_non_importerror_does_not_escape(self, monkeypatch):
        """The exact OC5 failure."""
        e = self._fail_import_with(
            monkeypatch, NameError("name 'torch' is not defined"),
        )
        assert e.is_available() is False

    def test_arbitrary_exception_types_are_survived(self, monkeypatch):
        """A broken native dep can surface almost anything; every
        Python-level failure means the same thing operationally."""
        for exc in (NameError("x"), RuntimeError("x"), OSError("x"),
                    AttributeError("x"), ValueError("x")):
            e = self._fail_import_with(monkeypatch, exc)
            assert e.is_available() is False, type(exc).__name__

    def test_embed_returns_none_rather_than_raising(self, monkeypatch):
        """Caller contract: embed() yields None and the agent carries on
        with FTS5-only retrieval."""
        e = self._fail_import_with(
            monkeypatch, NameError("name 'torch' is not defined"),
        )
        assert e.embed("anything at all") is None
