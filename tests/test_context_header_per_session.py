"""Regression for the context gas-tank fill calculation.

Two bugs, two eras, both produce a false 🔴 0% that makes a healthy
bot act broken — the LLM sees 🔴 in the prior turn's header (which is
part of conversation history), emulates "I'm out of context," and
starts returning terse "I'm not responding" replies. On a grandma's
one long conversation that is an un-troubleshootable dead agent.

  1. (fixed 2026-04-28) a PROCESS-GLOBAL counter accumulated across
     ALL sessions → 🔴 after ~50 conversations. Fix: track per
     session_id.

  2. (fixed 2026-07-06) WITHIN a session the counter still SUMMED
     each turn's ``input_tokens``. But input_tokens already counts the
     whole prompt (system + tools + full history + memory) every turn,
     so summing N-counted the history and drained the tank ~Nx too
     fast — a handful of short turns pushed a barely-used 200K window
     to 🔴 (observed live: 97%→51% over 5 short grandma turns). Fix:
     the session's fill is the running MAX of each turn's footprint
     (input+output), not a sum. input_tokens IS the current window
     fill; you don't add it to itself.

This locks both contracts: per-session isolation AND footprint-not-sum.
"""

from __future__ import annotations

import pytest

from windyfly.agent.loop import _record_session_footprint, _session_tokens

# Opt this file out of the conftest autouse that identity-stubs
# maybe_prepend_header — these tests verify per-session token
# tracking interacts correctly with the gas-tank header.
pytestmark = pytest.mark.state_emoji_prefix


@pytest.fixture(autouse=True)
def clear_session_state():
    _session_tokens.clear()
    yield
    _session_tokens.clear()


def test_first_call_for_session_reports_that_turns_footprint():
    fill = _record_session_footprint("alice-session-1", 1000)
    assert fill == 1000


def test_within_session_tracks_max_footprint_not_a_sum():
    # Each value is a WHOLE-PROMPT token count for that turn (already
    # includes all prior history). The window fill is the largest, not
    # the sum — summing (→ 1750) was the drain-too-fast bug.
    _record_session_footprint("alice-session-1", 1000)
    _record_session_footprint("alice-session-1", 1500)
    fill = _record_session_footprint("alice-session-1", 1200)
    assert fill == 1500


def test_footprint_is_monotonic_never_refills_on_a_dip():
    _record_session_footprint("alice-session-1", 40_000)
    # a transient provider undercount must NOT make the tank appear to
    # refill mid-conversation
    fill = _record_session_footprint("alice-session-1", 25_000)
    assert fill == 40_000


def test_different_sessions_do_not_pollute_each_other():
    _record_session_footprint("alice-session-1", 100_000)
    # bob's first turn must NOT see alice's fill
    bob_fill = _record_session_footprint("bob-session-1", 1000)
    assert bob_fill == 1000
    # alice unaffected by bob
    alice_fill = _record_session_footprint("alice-session-1", 500)
    assert alice_fill == 100_000


def test_short_grandma_chat_stays_green():
    """The live-observed failure: short turns on a big-prompt agent.
    Each turn's whole-prompt footprint grows slowly as history accrues
    (~22K, 24K, 26K …). With MAX semantics on a 200K window the tank is
    ~87% after several turns — green. Summing would have it near 🔴."""
    footprints = [22_000, 24_000, 26_000, 28_000, 30_000]
    fill = 0
    for fp in footprints:
        fill = _record_session_footprint("grandma-1", fp)
    max_ctx = 200_000
    pct_remaining = 100.0 - (fill / max_ctx) * 100
    assert fill == 30_000  # the largest footprint, not the 130K sum
    assert pct_remaining >= 80  # comfortably green, not a false 🔴
