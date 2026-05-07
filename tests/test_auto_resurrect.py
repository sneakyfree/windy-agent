"""Auto-resurrect (PR #145) regression tests.

Pin the contract:

  - Default state: ENABLED (no flag = on; the failure mode of
    auto-resurrect being on-by-default is "user sees notification"
    not "bot crashes")
  - set_auto_resurrect(False) writes the disable flag; True clears
  - is_auto_resurrect_disabled() respects the flag
  - 60s cooldown blocks rapid-fire attempts (zombie-loop guard)
  - auto_resurrect_attempt() returns:
      ok=True + model when Ollama is available + no cooldown
      ok=False reason='disabled' when user opted out
      ok=False reason='cooldown' when within 60s of last attempt
      ok=False from underlying resurrect() when Ollama unavailable
  - On Ollama-unavailable, the resurrection flag is NOT written
    (no lying about being "back")
  - agent_respond chain-fail with auto-resurrect ON + Ollama
    available → notification prepended + Ollama route
  - agent_respond chain-fail with auto-resurrect OFF → standard
    offline_response, no notification
  - agent_respond chain-fail when Ollama unavailable → standard
    offline_response, no notification (user opt-in via /resurrect)
  - Slash-command parser recognizes all aliases (/auto-resurrect,
    /auto-rescue, /autoresurrect)
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from windyfly.agent import resurrect as _r


@pytest.fixture(autouse=True)
def isolated_flags(monkeypatch, tmp_path):
    """Per-test isolation for resurrect, auto-resurrect-disable,
    and cooldown-marker flags."""
    monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".resurrected"))
    monkeypatch.setenv("WINDY_AUTO_RESURRECT_DISABLED", str(tmp_path / ".auto_disabled"))
    monkeypatch.setenv("WINDY_AUTO_RESURRECT_LAST", str(tmp_path / ".auto_last"))
    yield tmp_path


# ── Default + toggle ──────────────────────────────────────────────


def test_default_is_enabled():
    """No flag file → auto-resurrect is ON. Default-on because most
    grandmas won't know to enable manually, and the failure mode is
    'user sees notification' not 'bot crashes'."""
    assert _r.is_auto_resurrect_disabled() is False


def test_set_disabled_writes_flag(isolated_flags):
    out = _r.set_auto_resurrect(False, actor="grant")
    assert out["ok"] is True
    assert out["enabled"] is False
    assert _r.is_auto_resurrect_disabled() is True
    assert (isolated_flags / ".auto_disabled").exists()


def test_set_enabled_clears_flag(isolated_flags):
    _r.set_auto_resurrect(False, actor="grant")
    assert _r.is_auto_resurrect_disabled() is True
    out = _r.set_auto_resurrect(True, actor="grant")
    assert out["ok"] is True
    assert out["enabled"] is True
    assert _r.is_auto_resurrect_disabled() is False


def test_enable_when_already_enabled_idempotent():
    out = _r.set_auto_resurrect(True)
    assert out["ok"] is True
    assert out["was_disabled"] is False


# ── Cooldown ──────────────────────────────────────────────────────


def test_cooldown_blocks_rapid_attempts(isolated_flags):
    """After one attempt, the next within 60s returns
    reason='cooldown' regardless of whether Ollama is available.
    Zombie-loop guard."""
    with patch.object(_r, "list_installed_ollama_models", return_value=[
        {"name": "llama3.2:3b", "size": 2_000_000_000},
    ]):
        out1 = _r.auto_resurrect_attempt()
    assert out1["ok"] is True
    # Reset the resurrect flag so the underlying resurrect() doesn't
    # short-circuit on second call (we want to test cooldown specifically).
    (isolated_flags / ".resurrected").unlink(missing_ok=True)

    out2 = _r.auto_resurrect_attempt()
    assert out2["ok"] is False
    assert out2["reason"] == "cooldown"


def test_cooldown_clears_after_60s(isolated_flags):
    """After ~60s, cooldown clears. Test by writing a stale marker."""
    # Write a marker timestamp from 90s ago
    (isolated_flags / ".auto_last").write_text(str(time.time() - 90))

    assert _r._within_auto_cooldown() is False


# ── Disabled short-circuit ────────────────────────────────────────


def test_disabled_returns_early_without_probing_ollama(isolated_flags):
    """When user opted out, auto-resurrect must NOT probe Ollama —
    that's wasted work. Verify by mocking the probe to raise."""
    _r.set_auto_resurrect(False, actor="test")
    with patch.object(_r, "list_installed_ollama_models",
                      side_effect=AssertionError("probe should not run")):
        out = _r.auto_resurrect_attempt()
    assert out["ok"] is False
    assert out["reason"] == "disabled"


# ── Ollama unavailable → don't lie ─────────────────────────────────


def test_no_flag_written_when_ollama_unavailable(isolated_flags):
    """If Ollama isn't running/installed, auto_resurrect_attempt
    returns ok=False AND the resurrection flag is NOT written.
    Critical: bot must not 'go into lifeboat' when there's no
    lifeboat to go into."""
    with patch.object(_r, "list_installed_ollama_models", return_value=[]):
        out = _r.auto_resurrect_attempt()
    assert out["ok"] is False
    assert out["reason"] in ("ollama_not_running", "no_models_installed")
    assert _r.is_resurrected() is False


# ── auto_resurrect_status ─────────────────────────────────────────


def test_status_reflects_enabled_state(isolated_flags):
    s = _r.auto_resurrect_status()
    assert s["enabled"] is True
    _r.set_auto_resurrect(False)
    s = _r.auto_resurrect_status()
    assert s["enabled"] is False


# ── Slash-command parser ──────────────────────────────────────────


class TestAutoResurrectParser:
    @staticmethod
    def _parse():
        from windyfly.channels.slash_commands import parse_auto_resurrect_command
        return parse_auto_resurrect_command

    def test_bare_returns_status(self):
        p = self._parse()
        assert p("/auto-resurrect") == (True, None)
        assert p("/AUTO-RESURRECT") == (True, None)
        assert p("  /auto-resurrect  ") == (True, None)

    def test_aliases_recognized(self):
        p = self._parse()
        for alias in ("/auto-rescue", "/autoresurrect"):
            assert p(alias) == (True, None)

    def test_on_off(self):
        p = self._parse()
        assert p("/auto-resurrect on") == (True, "on")
        assert p("/auto-resurrect off") == (True, "off")
        assert p("/auto-resurrect enable") == (True, "on")
        assert p("/auto-resurrect disable") == (True, "off")

    def test_invalid_arg(self):
        p = self._parse()
        assert p("/auto-resurrect maybe") == (True, "invalid")

    def test_unrelated(self):
        p = self._parse()
        assert p("/resurrect") == (False, None)  # different command
        assert p("hello") == (False, None)
        assert p(None) == (False, None)
        assert p("") == (False, None)


# ── Integration: agent_respond chain-fail path ────────────────────


def _make_config():
    return {
        "agent": {"default_model": "claude-haiku-4-5-20251001",
                  "max_context_tokens": 8000, "max_response_tokens": 2000,
                  "temperature": 0.7},
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 20,
                   "max_nodes_per_context": 10},
        "personality": {"soul_path": "SOUL.md", "humor_level": 7,
                        "formality": 4, "proactivity": 5, "verbosity": 5,
                        "reasoning_depth": 6, "autonomy": 3,
                        "epistemic_strictness": 5},
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }


@pytest.fixture
def stack():
    from windyfly.memory.database import Database
    from windyfly.memory.episodes import save_episode
    from windyfly.memory.write_queue import WriteQueue
    db = Database(":memory:")
    save_episode(db, "user", "bootstrap", session_id="bootstrap")  # bypass welcome
    wq = WriteQueue(); wq.start()
    yield _make_config(), db, wq
    try: wq.stop()
    except Exception: pass
    db.close()


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_chain_fail_with_auto_on_and_ollama_available_prepends_notification(
    mock_llm, _online, stack, isolated_flags,
):
    """The big one: chain exhaustion + auto-resurrect ON + Ollama
    available → bot returns reply with notification PLUS Ollama
    output."""
    config, db, wq = stack
    from windyfly.agent.loop import agent_respond

    mock_llm.side_effect = RuntimeError(
        "LLM call failed across all providers in chain (attempted=['anthropic']): 401"
    )

    # Make Ollama "available" with a model
    with patch("windyfly.agent.resurrect.list_installed_ollama_models",
               return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}]), \
         patch("windyfly.agent.offline.is_ollama_available", return_value=True), \
         patch("windyfly.agent.offline._call_ollama",
               return_value="It's nice today!"):
        response = agent_respond(config, db, wq, "What's the weather?", "test-1")

    # Notification present (rate-limit explanation + how to opt out)
    assert "auto-switched" in response.lower()
    assert "rate limit" in response.lower()
    assert "/normal" in response
    assert "/auto-resurrect off" in response
    # And the actual Ollama response
    assert "It's nice today!" in response


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_chain_fail_with_auto_off_no_notification(
    mock_llm, _online, stack, isolated_flags,
):
    """When user has opted out, chain-fail goes straight to the
    standard offline_response — no auto-switch, no notification."""
    config, db, wq = stack
    _r.set_auto_resurrect(False, actor="test")
    from windyfly.agent.loop import agent_respond

    mock_llm.side_effect = RuntimeError(
        "LLM call failed across all providers in chain: 401"
    )

    response = agent_respond(config, db, wq, "Hi", "test-2")

    # Standard offline message; no auto-switch notification
    assert "auto-switched" not in response.lower()
    assert "currently offline" in response.lower() or "🪰" in response


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_chain_fail_when_ollama_unavailable_no_notification(
    mock_llm, _online, stack, isolated_flags,
):
    """When Ollama isn't installed, auto-resurrect attempt fails
    silently — user gets the standard offline message (which has
    the PR #141 recovery footer pointing at /resurrect for manual
    trigger)."""
    config, db, wq = stack
    from windyfly.agent.loop import agent_respond

    mock_llm.side_effect = RuntimeError(
        "LLM call failed across all providers in chain: 401"
    )
    with patch("windyfly.agent.resurrect.list_installed_ollama_models",
               return_value=[]):
        response = agent_respond(config, db, wq, "Hi", "test-3")

    # No "auto-switched" notification (the attempt failed)
    assert "auto-switched" not in response.lower()
    # Standard offline message
    assert "currently offline" in response.lower() or "🪰" in response
    # PR #141 recovery footer should still be present
    assert "/reset" in response or "/resurrect" in response
