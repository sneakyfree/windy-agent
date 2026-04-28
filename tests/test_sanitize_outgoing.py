"""Regression tests for the universal outgoing-message sanitizer.

Each grandma-tour scenario gets a regression test. The bar is "show
this output to a 75-year-old who has never used a computer — would
they be confused, scared, or embarrassed?" — and if so, the sanitizer
must catch it.
"""

from __future__ import annotations

from windyfly.observability.sanitize import (
    TELEGRAM_MAX_MESSAGE,
    sanitize_outgoing,
)


class TestPrimaryFailureModes:
    def test_none_input_returns_polite_fallback(self):
        out = sanitize_outgoing(None)
        assert out
        assert "try again" in out.lower()

    def test_empty_string_returns_polite_fallback(self):
        out = sanitize_outgoing("")
        assert out
        assert "try again" in out.lower()

    def test_whitespace_only_returns_polite_fallback(self):
        out = sanitize_outgoing("   \n\t  \n  ")
        assert out
        assert "try again" in out.lower()

    def test_non_string_input_does_not_raise(self):
        out = sanitize_outgoing(12345)  # type: ignore[arg-type]
        assert out  # falls back to str() coerce, then keeps the digits

    def test_valid_short_reply_passes_through_unchanged(self):
        text = "Hi Grandma! It's a sunny day."
        assert sanitize_outgoing(text) == text


class TestTracebackStripping:
    def test_full_python_traceback_stripped(self):
        text = (
            "Here's what I found:\n\n"
            'Traceback (most recent call last):\n'
            '  File "loop.py", line 465, in agent_respond\n'
            '    result = call_llm(...)\n'
            "RuntimeError: provider chain exhausted\n\n"
            "But anyway, the answer is 42."
        )
        out = sanitize_outgoing(text)
        assert "Traceback" not in out
        assert "RuntimeError" not in out
        assert "loop.py" not in out
        assert "answer is 42" in out

    def test_traceback_only_returns_fallback(self):
        """If the entire reply is a stack trace, no user-content
        survives — return the polite fallback rather than empty."""
        text = (
            'Traceback (most recent call last):\n'
            '  File "x.py", line 1, in <module>\n'
            '    raise ValueError("boom")\n'
            "ValueError: boom"
        )
        out = sanitize_outgoing(text)
        assert "Traceback" not in out
        assert "ValueError" not in out
        assert out  # non-empty fallback

    def test_error_prefix_stripped(self):
        text = "Error: something went wrong but here's the answer."
        out = sanitize_outgoing(text)
        assert not out.lower().startswith("error:")
        assert "answer" in out


class TestCredentialRedaction:
    def test_anthropic_oauth_token_redacted(self):
        text = "Debug info: ANTHROPIC_API_KEY=sk-ant-oat01-VwUdywrPUNW2MlOu4FNOPGhg3P3-hc6z-z8wplHFQkOg"
        out = sanitize_outgoing(text)
        assert "VwUdywrPUNW2MlOu4FNOPGhg3P3" not in out

    def test_telegram_bot_token_redacted(self):
        text = "URL: https://api.telegram.org/bot8669155077:AAEsupersecret_redacted_value_xxxxxxxxxxxxxxxxxxxx/getMe"
        out = sanitize_outgoing(text)
        assert "AAEsupersecret_redacted_value_xxxxxxxxxxxxxxxxxxxx" not in out


class TestControlChars:
    def test_null_byte_stripped(self):
        text = "hello\x00world"
        out = sanitize_outgoing(text)
        assert "\x00" not in out
        assert "hello" in out and "world" in out

    def test_newline_and_tab_preserved(self):
        text = "line1\nline2\tindented"
        out = sanitize_outgoing(text)
        assert "\n" in out
        assert "\t" in out

    def test_excess_newlines_collapsed(self):
        text = "para1\n\n\n\n\n\n\n\n\npara2"
        out = sanitize_outgoing(text)
        assert "\n\n\n\n" not in out


class TestTruncation:
    def test_long_message_truncated_to_telegram_limit(self):
        text = "x" * 10_000
        out = sanitize_outgoing(text)
        assert len(out) <= TELEGRAM_MAX_MESSAGE
        assert "truncated" in out.lower()

    def test_at_limit_no_marker(self):
        text = "y" * TELEGRAM_MAX_MESSAGE
        out = sanitize_outgoing(text)
        assert len(out) <= TELEGRAM_MAX_MESSAGE
        # No truncation needed when exactly at the limit
        assert "truncated" not in out.lower()


class TestRobustness:
    def test_pathological_input_does_not_raise(self):
        """Sanitizer must never raise. Throw weird stuff at it."""
        weirdness = [
            "\x00" * 5000,
            "Traceback " * 100,
            "sk-ant-oat01-" + "A" * 200,
            "\n" * 10000,
            "🦊" * 5000,
            "<script>alert(1)</script>" * 100,
        ]
        for w in weirdness:
            out = sanitize_outgoing(w)
            assert isinstance(out, str)
            assert len(out) <= TELEGRAM_MAX_MESSAGE
            assert out  # always non-empty
