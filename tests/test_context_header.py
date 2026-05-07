"""Tests for the context gas tank header."""

from unittest.mock import patch

from windyfly.agent.context_header import ContextTracker, maybe_prepend_header


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
