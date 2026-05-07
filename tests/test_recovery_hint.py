"""Centralized recovery-hint regression tests.

PR #141 ships ``with_recovery_hint`` to standardize the "/reset
or /resurrect" footer across every error path. This suite pins:

  - Empty / None input is returned unchanged (nothing to attach to)
  - A normal error message gets the footer appended
  - The footer mentions BOTH /reset (mild recovery) and /resurrect
    (heavy recovery) so confused grandma has graduated options
  - Idempotent guard: messages already mentioning /reset OR
    /resurrect are returned unchanged (no double-hint)
  - Italic Markdown wrapping so Telegram's parse_mode='Markdown'
    renders the hint as a visually-distinct line
  - Tested against the actual offline-mode message and one of the
    typical telegram_bot ack strings so a regression in either
    site fails this file
"""

from __future__ import annotations

from windyfly.observability.recovery_hint import RECOVERY_HINT, with_recovery_hint


# ── Edge inputs ────────────────────────────────────────────────────


def test_empty_input_unchanged():
    assert with_recovery_hint("") == ""


def test_none_input_returns_empty_string():
    assert with_recovery_hint(None) == ""


def test_whitespace_only_unchanged():
    """Whitespace-only is treated as empty — nothing to attach to."""
    # Note: pure whitespace technically isn't "empty" by truthiness,
    # but the helper's contract is "if the message is meaningful,
    # attach". Whitespace-only input is allowed through as-is —
    # downstream code handles it.
    assert with_recovery_hint("   ") == "   "  # passes through (no recovery info anyway)


# ── Footer attachment ──────────────────────────────────────────────


def test_normal_error_gets_footer():
    msg = "⚠ Couldn't read the cost ledger right now."
    out = with_recovery_hint(msg)
    assert msg in out
    assert RECOVERY_HINT in out
    # Footer is on its own paragraph (two newlines separation)
    assert "\n\n" in out


def test_footer_mentions_both_recovery_commands():
    """Pin both /reset (restart, mild) and /resurrect (free model,
    heavy) so a panicked grandma has graduated options. If a future
    refactor pulls one out — fail fast."""
    assert "/reset" in RECOVERY_HINT
    assert "/resurrect" in RECOVERY_HINT


def test_footer_uses_italic_markdown():
    """Telegram parse_mode='Markdown' renders _italic_. Pin the
    underscore wrapping so the hint visually separates from the
    error body."""
    assert RECOVERY_HINT.startswith("_") and RECOVERY_HINT.endswith("_")


# ── Idempotent guard ──────────────────────────────────────────────


def test_skips_when_message_already_mentions_reset():
    """Pause flag-write failure already says 'use /reset instead' —
    we don't want to append a redundant footer that mentions /reset
    AGAIN."""
    msg = "⚠ Couldn't write the pause flag — please use /reset instead."
    assert with_recovery_hint(msg) == msg
    assert with_recovery_hint(msg).count("/reset") == 1


def test_skips_when_message_already_mentions_resurrect():
    """Lifeboat-mode acks already mention /resurrect. Don't double up."""
    msg = "🛟 Lifeboat mode activated. Say /normal when /resurrect served its purpose."
    assert with_recovery_hint(msg) == msg


def test_skips_case_insensitive():
    """Some acks may have /Reset or /RESET (Title-cased / shouted).
    The guard should match regardless of case."""
    assert with_recovery_hint("Try /Reset to fix it.") == "Try /Reset to fix it."
    assert with_recovery_hint("HIT /RESURRECT") == "HIT /RESURRECT"


# ── Idempotency on already-wrapped input ───────────────────────────


def test_double_call_does_not_duplicate_footer():
    """with_recovery_hint(with_recovery_hint(msg)) must not produce
    two footers stacked. The first call adds the footer (which
    contains /reset and /resurrect); the second call sees those
    tokens and skips."""
    msg = "⚠ Something failed."
    once = with_recovery_hint(msg)
    twice = with_recovery_hint(once)
    assert once == twice
    assert once.count("/reset") == 1
    assert once.count("/resurrect") == 1


# ── Integration with actual call sites ────────────────────────────


def test_offline_message_gets_hint():
    """Pin the offline.get_offline_response → with_recovery_hint
    integration. Without this, an offline grandma sees a vague
    'I'm currently offline' with no idea what to do."""
    # Test the REAL string returned by the offline path with no
    # Ollama available.
    from windyfly.agent.offline import get_offline_response
    from unittest.mock import patch
    with patch("windyfly.agent.offline.is_ollama_available", return_value=False):
        out = get_offline_response("hello")
    assert "currently offline" in out.lower()
    assert "/reset" in out
    assert "/resurrect" in out


def test_typical_telegram_error_gets_hint():
    """Sanity-test against one of the patterns telegram_bot.py
    wraps: the spend-ledger-failure ack."""
    msg = "⚠ Couldn't read the cost ledger right now."
    out = with_recovery_hint(msg)
    # Body preserved intact
    assert "Couldn't read the cost ledger" in out
    # Hint added
    assert "/reset" in out
    assert "/resurrect" in out


# ── Footer text quality ───────────────────────────────────────────


def test_footer_is_one_line():
    """The footer should be a single rendered line (no embedded
    newlines) — multi-line footers eat vertical screen space and
    push the original error off-screen on small phones."""
    assert "\n" not in RECOVERY_HINT


def test_footer_under_120_chars():
    """Keep the footer short enough that it doesn't eat half the
    Telegram message budget when added to a long error."""
    assert len(RECOVERY_HINT) <= 120, (
        f"recovery hint is {len(RECOVERY_HINT)} chars; keep it under 120"
    )
