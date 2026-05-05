"""Channel-agnostic slash-command parser regression tests.

The text-recognition layer extracted from telegram_bot.py in PR #130.
Covers the same recognition that future Matrix / iMessage / WhatsApp
adapters will consume verbatim. Each parser must:

  - Recognize all documented aliases for its command
  - Reject near-misses (e.g., "/pauses" must NOT match /pause)
  - Be case-insensitive
  - Trim whitespace
  - Handle None and empty input without crashing
  - Treat trailing/embedded slash text as different commands
"""

from __future__ import annotations

import pytest

from windyfly.channels import slash_commands as sc


# ── Edge-case input shapes ─────────────────────────────────────────


@pytest.mark.parametrize("recognizer", [
    sc.is_panic_message,
    sc.is_pause_message,
    sc.is_resume_message,
    sc.is_spend_message,
    sc.is_version_message,
    sc.is_uptime_message,
    sc.is_whoami_message,
])
def test_none_input_returns_false(recognizer):
    assert recognizer(None) is False


@pytest.mark.parametrize("recognizer", [
    sc.is_panic_message,
    sc.is_pause_message,
    sc.is_resume_message,
    sc.is_spend_message,
    sc.is_version_message,
    sc.is_uptime_message,
    sc.is_whoami_message,
])
def test_empty_input_returns_false(recognizer):
    assert recognizer("") is False
    assert recognizer("   ") is False


# ── /reset / panic ─────────────────────────────────────────────────


@pytest.mark.parametrize("input_text", [
    "/reset", "/panic", "/nuclear", "🆘",
    "/Reset", "  /reset  ", "/RESET",
    "my agent is broken",
    "my bot is broken",
    "agent is stuck",
    "bot is stuck",
    "please nuclear reset this thing",
    "factory reset",
    "Bring my agent back, please!",
    "BRING BACK MY AGENT now",
])
def test_panic_recognized(input_text):
    assert sc.is_panic_message(input_text) is True


@pytest.mark.parametrize("input_text", [
    "/reset my password",  # has trailing text → exact-match fails
    "hello",
    "I love resets",  # phrase mismatch
    "/pause",  # different command
    "factory",  # partial phrase only
    "stuck on a problem",  # no agent/bot reference
])
def test_panic_not_recognized(input_text):
    assert sc.is_panic_message(input_text) is False


# ── /pause aliases ─────────────────────────────────────────────────


@pytest.mark.parametrize("input_text", [
    "/pause", "/stop-spending", "/stop",
    "/Pause", "  /pause  ", "/STOP",
])
def test_pause_recognized(input_text):
    assert sc.is_pause_message(input_text) is True


@pytest.mark.parametrize("input_text", [
    "/pauses",  # near-miss
    "/pause now",  # has trailing
    "pause",  # no slash
    "/resume",  # opposite command
    "stop",  # no slash
])
def test_pause_not_recognized(input_text):
    assert sc.is_pause_message(input_text) is False


# ── /resume aliases ────────────────────────────────────────────────


@pytest.mark.parametrize("input_text", [
    "/resume", "/wake-up", "/wake",
    "/Resume", "  /wake  ",
])
def test_resume_recognized(input_text):
    assert sc.is_resume_message(input_text) is True


@pytest.mark.parametrize("input_text", [
    "/resumes", "/resume now", "resume",
    "/wakeup",  # missing hyphen
])
def test_resume_not_recognized(input_text):
    assert sc.is_resume_message(input_text) is False


# ── /spend aliases ─────────────────────────────────────────────────


@pytest.mark.parametrize("input_text", [
    "/spend", "/usage", "/burn",
    "/Spend", "/BURN",
])
def test_spend_recognized(input_text):
    assert sc.is_spend_message(input_text) is True


@pytest.mark.parametrize("input_text", [
    "/spends", "/spend report", "spend",
    "/burned",
])
def test_spend_not_recognized(input_text):
    assert sc.is_spend_message(input_text) is False


# ── /version, /v ──────────────────────────────────────────────────


@pytest.mark.parametrize("input_text", [
    "/version", "/v", "/Version", "/V",
    "  /version  ",
])
def test_version_recognized(input_text):
    assert sc.is_version_message(input_text) is True


@pytest.mark.parametrize("input_text", [
    "/versions",  # near-miss
    "/version please",
    "/ver",
    "version",
    "/vv",
])
def test_version_not_recognized(input_text):
    assert sc.is_version_message(input_text) is False


# ── /uptime ───────────────────────────────────────────────────────


@pytest.mark.parametrize("input_text", [
    "/uptime", "/Uptime", " /UPTIME ",
])
def test_uptime_recognized(input_text):
    assert sc.is_uptime_message(input_text) is True


@pytest.mark.parametrize("input_text", [
    "/up", "/uptimes", "uptime",
])
def test_uptime_not_recognized(input_text):
    assert sc.is_uptime_message(input_text) is False


# ── /whoami, /identity ────────────────────────────────────────────


@pytest.mark.parametrize("input_text", [
    "/whoami", "/identity",
    "/Whoami", "/IDENTITY",
])
def test_whoami_recognized(input_text):
    assert sc.is_whoami_message(input_text) is True


@pytest.mark.parametrize("input_text", [
    "/who", "/me", "whoami",
    "/whoamI?",
])
def test_whoami_not_recognized(input_text):
    assert sc.is_whoami_message(input_text) is False


# ── Mutual exclusivity ────────────────────────────────────────────


@pytest.mark.parametrize("input_text,expected_recognizer", [
    ("/pause", sc.is_pause_message),
    ("/resume", sc.is_resume_message),
    ("/spend", sc.is_spend_message),
    ("/version", sc.is_version_message),
    ("/uptime", sc.is_uptime_message),
    ("/whoami", sc.is_whoami_message),
    ("/reset", sc.is_panic_message),
])
def test_command_only_matches_its_own_recognizer(input_text, expected_recognizer):
    """No two recognizers should match the same exact-form command.
    /pause must NOT trip /resume, etc."""
    all_recognizers = [
        sc.is_panic_message,
        sc.is_pause_message,
        sc.is_resume_message,
        sc.is_spend_message,
        sc.is_version_message,
        sc.is_uptime_message,
        sc.is_whoami_message,
    ]
    matched = [r for r in all_recognizers if r(input_text)]
    assert matched == [expected_recognizer], (
        f"{input_text!r} should match exactly {expected_recognizer.__name__}; "
        f"actually matched {[r.__name__ for r in matched]}"
    )


# ── Constants exposed for channel-side use ───────────────────────


def test_panic_constants_exposed():
    """Channel adapters that want to introspect the alias set (e.g.,
    for help text) need access to the underlying frozenset."""
    assert "/reset" in sc.PANIC_EXACT
    assert "🆘" in sc.PANIC_EXACT
    assert "my bot is broken" in sc.PANIC_PHRASES


def test_pause_constants_exposed():
    assert "/pause" in sc.PAUSE_EXACT
    assert "/stop" in sc.PAUSE_EXACT
    assert "/resume" in sc.RESUME_EXACT


def test_introspection_constants_exposed():
    assert "/version" in sc.VERSION_EXACT
    assert "/v" in sc.VERSION_EXACT
    assert "/uptime" in sc.UPTIME_EXACT
    assert "/whoami" in sc.WHOAMI_EXACT
    assert "/identity" in sc.WHOAMI_EXACT
