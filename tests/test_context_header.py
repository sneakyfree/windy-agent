"""Tests for the context gas tank header."""

from unittest.mock import patch

import pytest

from windyfly.agent.context_header import ContextTracker, maybe_prepend_header

# Opt this entire file out of the conftest autouse that
# identity-stubs maybe_prepend_header — these tests specifically
# verify the panel + state-emoji-prefix behavior.
pytestmark = pytest.mark.state_emoji_prefix


class TestContextTracker:
    """Test the ContextTracker class."""

    def test_pct_remaining_full(self) -> None:
        tracker = ContextTracker(max_context_tokens=200_000)
        assert tracker.pct_remaining == 100.0

    def test_pct_remaining_half(self) -> None:
        tracker = ContextTracker(max_context_tokens=200_000)
        tracker.tokens_used = 100_000
        assert tracker.pct_remaining == 50.0

    def test_pct_remaining_empty(self) -> None:
        tracker = ContextTracker(max_context_tokens=200_000)
        tracker.tokens_used = 200_000
        assert tracker.pct_remaining == 0.0

    def test_pct_remaining_over(self) -> None:
        tracker = ContextTracker(max_context_tokens=200_000)
        tracker.tokens_used = 250_000
        assert tracker.pct_remaining == 0.0  # Clamped to 0

    def test_should_show_header_first_time(self) -> None:
        """First response always shows header (time delta > 1h from epoch)."""
        tracker = ContextTracker()
        assert tracker.should_show_header() is True

    def test_should_show_header_after_10pct_delta(self) -> None:
        """Header shows after 10%+ context delta."""
        tracker = ContextTracker(max_context_tokens=200_000)
        # Show first header at 100%
        tracker.format_header()
        # Use 10% of context
        tracker.tokens_used = 20_001  # Just over 10%
        # Should trigger because delta > 10%
        with patch("windyfly.agent.context_header.time") as mock_time:
            mock_time.time.return_value = tracker._last_header_time + 30  # 30 sec later
            assert tracker.should_show_header() is True

    def test_no_header_rapid_fire(self) -> None:
        """No header on rapid messages with < 10% delta."""
        tracker = ContextTracker(max_context_tokens=200_000)
        # Show first header
        tracker.format_header()
        # Small token use (< 10%)
        tracker.tokens_used = 10_000  # 5%
        with patch("windyfly.agent.context_header.time") as mock_time:
            mock_time.time.return_value = tracker._last_header_time + 30  # 30 sec later
            assert tracker.should_show_header() is False

    def test_header_after_one_hour(self) -> None:
        """Header shows after 1h+ even with < 10% delta."""
        tracker = ContextTracker(max_context_tokens=200_000)
        tracker.format_header()
        tracker.tokens_used = 5_000  # Only 2.5%
        with patch("windyfly.agent.context_header.time") as mock_time:
            mock_time.time.return_value = tracker._last_header_time + 3601  # 1h+1s
            assert tracker.should_show_header() is True

    def test_format_header_green(self) -> None:
        tracker = ContextTracker(max_context_tokens=200_000)
        tracker.tokens_used = 0  # 100% remaining
        header = tracker.format_header()
        assert "🟢" in header
        assert "100%" in header
        assert "Windy Fly" in header

    def test_format_header_yellow(self) -> None:
        tracker = ContextTracker(max_context_tokens=200_000)
        tracker.tokens_used = 140_000  # 30% remaining
        header = tracker.format_header()
        assert "🟡" in header
        assert "30%" in header

    def test_format_header_red(self) -> None:
        tracker = ContextTracker(max_context_tokens=200_000)
        tracker.tokens_used = 190_000  # 5% remaining
        header = tracker.format_header()
        assert "🔴" in header
        assert "5%" in header


class TestMaybePrependHeader:
    """Test the maybe_prepend_header convenience function."""

    def test_prepends_on_first_call(self) -> None:
        # Reset singleton
        import windyfly.agent.context_header as ch
        ch._tracker = None
        result = maybe_prepend_header("Hello!", 0)
        assert result.startswith("[🪰 Windy Fly")
        assert "Hello!" in result

    def test_header_contains_response(self) -> None:
        import windyfly.agent.context_header as ch
        ch._tracker = None
        result = maybe_prepend_header("Test response text", 0)
        assert "Test response text" in result


class TestSingleEmojiPrefix:
    """PR #144 — always-on single-emoji health prefix on every reply."""

    def test_single_emoji_prefix_when_no_threshold_fires(self) -> None:
        """Reply with no major context drift gets just the emoji
        prefix, NOT the full panel."""
        import windyfly.agent.context_header as ch
        ch._tracker = None
        # First call shows full panel (1+ hour since "last" / 100%
        # delta from default). Second call without delta should drop
        # to single-emoji prefix.
        maybe_prepend_header("Setup call", 0)  # primes the tracker
        result = maybe_prepend_header("Second reply", 100)  # tiny delta
        # Should be single-emoji prefix, NOT the bracketed panel
        assert not result.startswith("[🪰")
        assert result.startswith("🟢 ")
        assert "Second reply" in result

    def test_emoji_prefix_handles_empty_response(self) -> None:
        """Empty response → returned unchanged. No point prefixing
        nothing."""
        import windyfly.agent.context_header as ch
        ch._tracker = None
        result = maybe_prepend_header("", 0)
        assert result == ""

    def test_emoji_changes_with_state(self) -> None:
        """🟢 → 🟡 → 🔴 as context burns down. Pinned because
        grandma's eye scans the first character; if that signal
        regresses, this test fails fast."""
        import windyfly.agent.context_header as ch

        # Healthy
        ch._tracker = None
        maybe_prepend_header("setup", 0)
        result = maybe_prepend_header("hi", 1000)  # ~99% remaining
        assert result.startswith("🟢 "), f"healthy should be 🟢, got: {result[:5]}"

        # Yellow zone (10-50%) — tokens_used = 150_000 of 200_000 = 25% remaining
        ch._tracker = None
        maybe_prepend_header("setup", 150_000)
        result = maybe_prepend_header("hi", 150_000)
        # Threshold won't fire on second call (no delta), so we get
        # the prefix
        assert result.startswith("🟡 ") or "🟡" in result[:20], (
            f"low context should be 🟡, got: {result[:20]}"
        )

    def test_resurrected_state_shows_lifeboat_emoji(self) -> None:
        """When the bot is in lifeboat mode (PR #138), the emoji
        prefix is 🛟 — surfacing 'paid creds dead' is more user-
        facing than memory pressure."""
        import windyfly.agent.context_header as ch
        from windyfly.agent import context_header as ch_mod

        ch._tracker = None
        # Patch the resurrection probe to True
        with patch.object(ch_mod, "_is_resurrected_safe", return_value=True):
            maybe_prepend_header("setup", 0)
            result = maybe_prepend_header("hi", 100)
        assert "🛟" in result[:5], (
            f"resurrected state should show 🛟 prefix, got: {result[:20]}"
        )

    def test_emoji_prefix_does_not_break_existing_panel_path(self) -> None:
        """When threshold fires (10%+ context delta), the FULL panel
        wins — single-emoji prefix is suppressed (the panel embeds
        the same emoji, so info isn't lost). Pin so a future refactor
        doesn't accidentally double-prefix."""
        import windyfly.agent.context_header as ch
        ch._tracker = None
        # Big delta from initial 100% → triggers full panel
        result = maybe_prepend_header("Hi!", 50_000)  # 25% used = 75% rem
        # Full panel format
        assert result.startswith("[🪰 Windy Fly")
        # NOT double-prefixed (i.e., no bare emoji + bracketed panel)
        assert not result.startswith("🟢 [")
        assert not result.startswith("🟡 [")


class TestEffectiveCapHonored:
    """PR #199 — ``maybe_prepend_header`` accepts ``max_tokens`` so
    the gas-tank reflects the per-channel effective cap (pinned via
    /memory, else model native). Pre-fix the tracker hardcoded 200K
    and a 1M-pinned channel hit 🔴 0% after ~30K tokens — the live
    bug reproduced 2026-05-19 on Grant's Windy 0."""

    def test_1M_cap_at_30K_used_shows_healthy(self) -> None:
        """The reproduction case: opus + /memory 1M + ~30K used
        should NOT show 🔴; it's only ~3% consumed."""
        import windyfly.agent.context_header as ch
        ch._tracker = None
        result = maybe_prepend_header(
            "reply", 30_000, max_tokens=1_000_000,
        )
        # 97% remaining → 🟢
        assert "🔴" not in result, (
            f"30K of 1M should NOT be 🔴; got: {result[:80]}"
        )
        # Tracker should reflect the passed cap
        tracker = ch.get_tracker()
        assert tracker.max_context_tokens == 1_000_000
        assert tracker.pct_remaining > 95

    def test_200K_cap_at_30K_used_shows_yellow(self) -> None:
        """Same 30K usage on a 200K cap is genuinely 🟡 (15% used,
        85% remaining)."""
        import windyfly.agent.context_header as ch
        ch._tracker = None
        # Establish baseline
        maybe_prepend_header("setup", 0, max_tokens=200_000)
        # Now 30K of 200K = 85% remaining → still 🟢 (≥ 50%)
        result = maybe_prepend_header("reply", 30_000, max_tokens=200_000)
        assert "🟢" in result
        # But 180K of 200K = 10% → 🟡
        result2 = maybe_prepend_header(
            "reply", 180_000, max_tokens=200_000,
        )
        assert "🟡" in result2 or "🔴" in result2

    def test_cap_can_change_mid_conversation(self) -> None:
        """User does /memory 1M when tracker was at 195K/200K (= 🔴).
        Next reply should reflect the new 1M cap (= ~80% remaining,
        🟢) — pinning the cap on each call avoids stale state."""
        import windyfly.agent.context_header as ch
        ch._tracker = None
        # Pre-cap-change: nearly full at 200K
        r1 = maybe_prepend_header("a", 195_000, max_tokens=200_000)
        assert "🔴" in r1 or "🟡" in r1
        # Post-/memory 1M: SAME tokens_used but new cap
        r2 = maybe_prepend_header("b", 195_000, max_tokens=1_000_000)
        # 195K of 1M = 80.5% remaining → 🟢
        assert "🟢" in r2, (
            f"After /memory 1M the bar should swing back to 🟢: {r2[:80]}"
        )

    def test_default_cap_backward_compat(self) -> None:
        """Calls that don't pass max_tokens still get the 200K
        default (pre-PR-199 callers won't break)."""
        import windyfly.agent.context_header as ch
        ch._tracker = None
        maybe_prepend_header("setup", 0)
        tracker = ch.get_tracker()
        assert tracker.max_context_tokens == 200_000


# === Engine transparency (2026-07-18) ===


class TestEngineInHeader:
    def test_panel_shows_engine_when_known(self):
        import windyfly.agent.context_header as ch
        ch._tracker = None
        out = ch.maybe_prepend_header("hi", 0, engine="claude-opus-4-8")
        # First call always shows the full panel (hour threshold unmet
        # history) — engine must be inside the panel brackets.
        assert "· claude-opus-4-8]" in out

    def test_panel_without_engine_unchanged(self):
        import windyfly.agent.context_header as ch
        ch._tracker = None
        out = ch.maybe_prepend_header("hi", 0)
        assert out.startswith("[🪰 Windy Fly · ")
        assert out.count("·") == 2  # ts + state only, no engine segment

    def test_engine_sticky_across_calls(self):
        # A call that doesn't pass engine (e.g. a command-ack path) must
        # not erase the last known engine.
        import windyfly.agent.context_header as ch
        ch._tracker = None
        ch.maybe_prepend_header("a", 0, engine="gpt-oss-120b")
        assert ch.get_tracker().last_engine == "gpt-oss-120b"
        ch.maybe_prepend_header("b", 10, engine=None)
        assert ch.get_tracker().last_engine == "gpt-oss-120b"

    def test_emoji_prefix_never_carries_engine(self):
        import windyfly.agent.context_header as ch
        ch._tracker = None
        t = ch.get_tracker()
        # Force the "no full panel" path: fresh header just shown.
        t._last_header_time = __import__("time").time()
        t._last_header_pct = 100.0
        out = ch.maybe_prepend_header("hello", 0, engine="claude-opus-4-8")
        assert out.startswith("🟢 ")
        assert "opus" not in out
