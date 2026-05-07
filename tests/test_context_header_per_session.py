"""Regression for context-tracker session scoping.

Pre-fix bug surfaced 2026-04-28 by stress_v7_endurance: a module-
global ``_session_tokens_used`` accumulated tokens across ALL
sessions for the lifetime of the process. After ~50 conversations
the context-header indicator showed 🔴 0% even though every
individual session had plenty of context. The LLM, seeing 🔴 0% in
the prior turn's header (which is part of conversation history),
emulated "I'm out of context" behavior and started returning terse
"I'm not responding" replies on healthy sessions.

This locks the new contract: tokens are tracked PER session_id, so
new sessions always start at 100%.
"""

from __future__ import annotations

import pytest

from windyfly.agent.loop import _bump_session_tokens, _session_tokens

# Opt this file out of the conftest autouse that identity-stubs
# maybe_prepend_header — these tests verify per-session token
# tracking interacts correctly with the gas-tank header.
pytestmark = pytest.mark.state_emoji_prefix


@pytest.fixture(autouse=True)
def clear_session_state():
    _session_tokens.clear()
    yield
    _session_tokens.clear()


def test_first_call_for_session_starts_at_zero():
    total = _bump_session_tokens("alice-session-1", 1000)
    assert total == 1000


def test_subsequent_calls_accumulate_within_session():
    _bump_session_tokens("alice-session-1", 1000)
    _bump_session_tokens("alice-session-1", 500)
    total = _bump_session_tokens("alice-session-1", 250)
    assert total == 1750


def test_different_sessions_do_not_pollute_each_other():
    _bump_session_tokens("alice-session-1", 50_000)
    _bump_session_tokens("alice-session-1", 50_000)
    # bob's first call: must NOT see alice's 100k tokens
    bob_total = _bump_session_tokens("bob-session-1", 1000)
    assert bob_total == 1000
    # alice continues unaffected by bob
    alice_total = _bump_session_tokens("alice-session-1", 500)
    assert alice_total == 100_500


def test_long_lived_process_with_many_sessions_does_not_drift():
    """The original bug: 50 sessions × 4k tokens each = 200k accumulated
    in the global, pushing the indicator to 🔴 0%. Per-session, every
    new session starts fresh."""
    for i in range(50):
        _bump_session_tokens(f"session-{i}", 4000)
    # New session #51 must see exactly 0 prior tokens.
    fresh = _bump_session_tokens("session-51", 1)
    assert fresh == 1
