"""memory.search + memory.read_range — the agent's key to its own past.

Chronicle Doctrine MUST-BUILD #2 (2026-07-18). Verifies the two
retrieval capabilities over a real episodes table.
"""
from __future__ import annotations

from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.agent.capabilities.memory_search import (
    register_memory_search_capabilities,
)
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode


def _reg_with_history():
    db = Database(":memory:")
    # A little conversation across two sessions / days.
    save_episode(db, "user", "Let's plan the Christmas party menu.",
                 session_id="telegram:1:v1")
    save_episode(db, "assistant", "Great — ham, or turkey?",
                 session_id="telegram:1:v1")
    save_episode(db, "user", "Ham. And ask Fred at the dealership about the truck.",
                 session_id="telegram:1:v1")
    save_episode(db, "user", "What's the weather tomorrow?",
                 session_id="telegram:1:v1")
    reg = CapabilityRegistry()
    register_memory_search_capabilities(reg, db, {})
    return reg, db


def _call(reg, cap_id, **kwargs):
    cap = reg.get(cap_id)
    assert cap is not None, f"{cap_id} not registered"
    return cap.handler(**kwargs)


class TestRegistration:
    def test_both_capabilities_registered(self):
        reg, _ = _reg_with_history()
        assert reg.get("memory.search") is not None
        assert reg.get("memory.read_range") is not None


class TestMemorySearch:
    def test_finds_topic_across_history(self):
        reg, _ = _reg_with_history()
        out = _call(reg, "memory.search", query="Christmas party")
        assert out["ok"] is True
        assert out["count"] >= 1
        joined = " ".join(r["content"] for r in out["results"]).lower()
        assert "christmas" in joined

    def test_hit_carries_surrounding_window(self):
        reg, _ = _reg_with_history()
        out = _call(reg, "memory.search", query="Fred dealership truck")
        assert out["count"] >= 1
        # at least one hit should expose its surrounding turns
        assert any("surrounding" in r for r in out["results"])

    def test_empty_query_rejected(self):
        reg, _ = _reg_with_history()
        out = _call(reg, "memory.search", query="   ")
        assert out["ok"] is False

    def test_no_match_is_honest_not_crash(self):
        reg, _ = _reg_with_history()
        out = _call(reg, "memory.search", query="zzzquantumwombat")
        assert out["ok"] is True
        assert out["count"] == 0
        assert "No matches" in out["hint"]


class TestMemoryReadRange:
    def test_hours_back_returns_recent_turns(self):
        reg, _ = _reg_with_history()
        out = _call(reg, "memory.read_range", hours_back=24)
        assert out["ok"] is True
        assert out["count"] >= 4
        # chronological
        whens = [t["when"] for t in out["turns"]]
        assert whens == sorted(whens)

    def test_requires_a_bound(self):
        reg, _ = _reg_with_history()
        out = _call(reg, "memory.read_range")
        assert out["ok"] is False

    def test_negative_hours_rejected(self):
        reg, _ = _reg_with_history()
        out = _call(reg, "memory.read_range", hours_back=-3)
        assert out["ok"] is False

    def test_max_turns_truncates_with_hint(self):
        reg, _ = _reg_with_history()
        out = _call(reg, "memory.read_range", hours_back=24, max_turns=2)
        assert out["count"] == 2
        assert out.get("truncated") is True
        assert "max_turns" in out["hint"]


class TestTranscriptOrdering:
    """A conversation must come back in the order it was said.

    ``episodes.created_at`` is ``CURRENT_TIMESTAMP`` — one-second
    resolution — so a question and its answer routinely carry the SAME
    timestamp. Ordering by ``created_at`` alone leaves those ties to
    the query planner. That is not theoretical: adding an index on
    ``episodes(session_id, created_at DESC)`` flipped the planner to an
    index scan and handed same-second ties back REVERSED, which
    surfaced as ``test_collaborators`` seeing 'assistant' where 'user'
    was expected.

    ``read_range`` is what the agent uses to re-read its own history
    after a reset, so reversed ties mean catching up on a conversation
    with every answer above its question. The explicit ``rowid``
    tiebreaker pins it regardless of planner or index.
    """

    def _db_same_second(self):
        import uuid
        db = Database(":memory:")
        convo = [
            ("user", "Q1 what time is the potluck?"),
            ("assistant", "A1 second Sunday"),
            ("user", "Q2 who is driving?"),
            ("assistant", "A2 Marla is driving"),
        ]
        for role, content in convo:
            db.execute(
                "INSERT INTO episodes (id, session_id, role, content, "
                "created_at) VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), "telegram:1:v1", role, content,
                 "2026-07-27 10:00:00"),
            )
        db.commit()
        return db, convo

    def test_read_range_preserves_order_within_one_second(self):
        db, convo = self._db_same_second()
        reg = CapabilityRegistry()
        register_memory_search_capabilities(reg, db, {})
        out = _call(reg, "memory.read_range", start="2026-01-01")
        got = [(t["role"], t["content"]) for t in out["turns"]]
        assert got == convo, got

    def test_order_survives_a_descending_index_on_created_at(self):
        """Pins the tiebreaker against the index that would actually
        reach this query.

        ``read_range`` filters on ``created_at`` alone (never
        session_id), so an index on ``(session_id, created_at)`` never
        applies to it — an earlier version of this test used that index,
        proved nothing, and passed against the unfixed code. An index on
        ``created_at DESC`` IS used here, and without the ``rowid``
        tiebreaker it walks the index backwards and returns same-second
        ties reversed. Verified by reverting the fix: this fails, the
        one above does not.
        """
        db, convo = self._db_same_second()
        db.execute("CREATE INDEX idx_ep_created ON episodes(created_at DESC)")
        db.commit()
        reg = CapabilityRegistry()
        register_memory_search_capabilities(reg, db, {})
        out = _call(reg, "memory.read_range", start="2026-01-01")
        got = [(t["role"], t["content"]) for t in out["turns"]]
        assert got == convo, got
