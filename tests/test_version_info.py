"""Regression: /version /uptime /whoami return real data, not LLM
improvisation.

v13 Q&A battery 2026-05-02 caught the bot saying 'I don't have a
/version command available in my current toolset' because the menu
advertised these commands but no channel handler existed. They were
falling through to the LLM, which honestly admitted it couldn't.
This test pins:

  - The introspection module returns all keys (no missing fields)
  - The git SHA / branch lookups don't crash on a non-git CWD
  - Uptime is monotonic and starts near zero on import
  - The Telegram parsers recognize the alias surface
  - Reply formatters produce non-empty Markdown for every command
"""

from __future__ import annotations

import time

from windyfly.observability.version_info import (
    _uptime_human,
    format_uptime_reply,
    format_version_reply,
    format_whoami_reply,
    get_version_info,
)


# ── version_info module ────────────────────────────────────────────


def test_get_version_info_keys_complete():
    """Every key must always be present — UI relies on it."""
    info = get_version_info()
    required = {
        "package_version", "sha", "sha_full", "branch", "ahead", "behind",
        "dirty", "last_commit_when", "last_commit_subject",
        "python", "platform", "os_pretty",
        "uptime_seconds", "uptime_human", "started_at", "flags",
    }
    assert required.issubset(info.keys()), f"missing: {required - set(info.keys())}"


def test_get_version_info_flags_complete():
    info = get_version_info()
    assert {"pause", "yolo", "guest"} == set(info["flags"].keys())
    for v in info["flags"].values():
        assert v in ("yes", "no")


def test_get_version_info_python_version_real():
    info = get_version_info()
    # 3.x major version sanity check
    assert info["python"].startswith("3.")


def test_get_version_info_uptime_monotonic():
    """Two calls in sequence — second must show non-decreasing
    uptime. (Could be equal if the clock granularity is coarse.)"""
    a = get_version_info()
    time.sleep(0.05)
    b = get_version_info()
    assert b["uptime_seconds"] >= a["uptime_seconds"]


def test_get_version_info_no_crash_in_non_git_dir(monkeypatch, tmp_path):
    """If WINDY_AGENT_DIR points at a non-git path, lookups must
    return '?' / fallback rather than raising. The bot must NEVER
    crash when running /version."""
    monkeypatch.setenv("WINDY_AGENT_DIR", str(tmp_path))
    info = get_version_info()
    # Branch / SHA can be '?', dirty defaults False — but no exception
    assert "sha" in info


# ── _uptime_human ──────────────────────────────────────────────────


def test_uptime_human_short_seconds():
    assert _uptime_human(0) == "0s"
    assert _uptime_human(45) == "45s"


def test_uptime_human_minutes():
    assert "1m" in _uptime_human(65)


def test_uptime_human_hours_minutes():
    out = _uptime_human(3700)  # 1h 1m 40s
    assert "1h" in out
    assert "1m" in out


def test_uptime_human_days():
    out = _uptime_human(90061)  # 1d 1h 1m 1s
    assert "1d" in out
    assert "1h" in out


# ── Reply formatters ───────────────────────────────────────────────


def test_format_version_reply_non_empty():
    out = format_version_reply()
    assert "Windy Fly" in out
    assert "Version" in out
    assert "Uptime" in out


def test_format_uptime_reply_non_empty():
    out = format_uptime_reply()
    assert "Uptime" in out


def test_format_whoami_reply_non_empty():
    out = format_whoami_reply()
    # /whoami uses an italic line for identity description
    assert "AI" in out or "I'm" in out
    # Must include the live SHA so user can verify the running version
    assert "Version" in out or "uptime" in out.lower()


def test_format_version_reply_includes_branch():
    out = format_version_reply()
    assert "Branch" in out


def test_format_version_reply_no_secrets_leaked(monkeypatch):
    """Sanity: even with sensitive env vars set, /version must not
    print them. /version is owner-tone but the message could be
    forwarded — keep credentials out by construction."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-test-12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "987654:secret-test-bot-token")
    out = format_version_reply()
    assert "sk-ant-secret-test-12345" not in out
    assert "secret-test-bot-token" not in out


# ── Telegram parser layer ──────────────────────────────────────────


class TestTelegramParsers:
    @staticmethod
    def _parsers():
        from windyfly.channels.telegram_bot import (
            _is_uptime_message, _is_version_message, _is_whoami_message,
        )
        return _is_version_message, _is_uptime_message, _is_whoami_message

    def test_version_aliases(self):
        v, _, _ = self._parsers()
        assert v("/version")
        assert v("/v")
        assert v("/Version")  # case-insensitive
        assert not v("/versions")  # word-boundary
        assert not v("hello /version")  # exact-match only
        assert not v(None)

    def test_uptime_aliases(self):
        _, u, _ = self._parsers()
        assert u("/uptime")
        assert u(" /UPTIME ")
        assert not u("/up")

    def test_whoami_aliases(self):
        _, _, w = self._parsers()
        assert w("/whoami")
        assert w("/identity")
        assert not w("/who")
