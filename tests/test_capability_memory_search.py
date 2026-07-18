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
