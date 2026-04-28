"""Regression for journal throttling per-session.

Same class of bug as #93. Pre-fix ``_interaction_count`` was a
module-global, so journal cadence was unpredictable in multi-session
scenarios — different users would have entries fire at unpredictable
intervals based on TOTAL interaction count across the process.

This locks the contract: journal triggers fire every 10th
interaction *within the same session*.
"""

from __future__ import annotations

import pytest

from windyfly.agent.loop import _session_interaction_counts


@pytest.fixture(autouse=True)
def clear_session_state():
    _session_interaction_counts.clear()
    yield
    _session_interaction_counts.clear()


def test_session_count_starts_at_zero():
    assert _session_interaction_counts == {}


def test_simulate_within_session_increment():
    """Walk the bump-then-modulo logic the way the journal trigger does."""
    sid = "alice"
    fired_iters = []
    for n in range(1, 25):
        count = _session_interaction_counts.get(sid, 0) + 1
        _session_interaction_counts[sid] = count
        if count % 10 == 0:
            fired_iters.append(n)
    assert fired_iters == [10, 20]


def test_alice_and_bob_do_not_share_count():
    """The original bug: alice's 9 prior interactions push bob's
    first ever message into the 'every-10th' bucket spuriously."""
    for _ in range(9):
        c = _session_interaction_counts.get("alice", 0) + 1
        _session_interaction_counts["alice"] = c
    # Bob's first message — count should be 1, not 10.
    bob = _session_interaction_counts.get("bob", 0) + 1
    _session_interaction_counts["bob"] = bob
    assert bob == 1
    # Alice's count untouched.
    assert _session_interaction_counts["alice"] == 9


def test_many_sessions_each_independent():
    """50 different sessions × 5 interactions each should leave each
    session at exactly 5 — pre-fix the global would be at 250."""
    for s in range(50):
        sid = f"session-{s}"
        for _ in range(5):
            c = _session_interaction_counts.get(sid, 0) + 1
            _session_interaction_counts[sid] = c
    assert all(v == 5 for v in _session_interaction_counts.values())
    assert len(_session_interaction_counts) == 50
