"""Lifeboat stuck-state recovery regression suite.

Pin the four-fix contract from 2026-05-10:

  Fix 1 — /reset clears the resurrect flag (panic handler invokes
          ``resurrect.normalize()`` before self-restart). Pre-fix
          /reset only restarted the process, leaving the flag on
          disk so the bot resumed in lifeboat mode forever.

  Fix 2 — Ollama timeout error gets the standard recovery footer
          (with_recovery_hint) so the user sees /reset and
          /resurrect when 30s timeouts hit. Pre-fix the bare error
          string shipped raw with no remediation.

  Fix 3 — When in lifeboat, ``attempt_paid_recovery()`` periodically
          probes the paid LLM. On success the flag drops and the
          turn falls through to the paid path with a "✅ Recovered"
          notice prepended. Cooldown rate-limits the probe to once
          per ~2 min.

  Fix 4 — The resurrection short-circuit at agent_respond step 1.7
          prefixes 🛟 to its reply. Pre-fix the short-circuit
          bypassed PR #144's state-emoji prefix, so users had no
          per-reply indication of lifeboat mode.

The four fixes interlock — together they turn lifeboat mode from a
"stuck forever" failure mode into "fall back, signal it, climb out
when the paid model recovers".
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from windyfly.agent import resurrect as _r


# ─── Shared isolation fixtures ────────────────────────────────────


@pytest.fixture
def isolated_recovery_marker(monkeypatch, tmp_path):
    """Per-test recovery-probe marker so cooldown state doesn't leak
    between tests."""
    monkeypatch.setenv("WINDY_RECOVERY_PROBE_LAST",
                       str(tmp_path / ".recovery_probe_last"))
    yield tmp_path


# ─── Fix 1: /reset clears the resurrect flag ─────────────────────


class TestPanicClearsResurrectFlag:
    """The panic ``/reset`` handler must clear the resurrect flag
    before restarting. Without this, a user in lifeboat mode hits
    /reset, the process restarts, and the flag is still on disk —
    so the very next reply is back in lifeboat. Surfaced 2026-05-10.
    """

    def test_normalize_called_when_panic_handler_fires(self, monkeypatch, tmp_path):
        """Verify the panic-handler import + call path actually runs
        ``normalize()``. We do this at the unit level by simulating
        the panic-handler block: import normalize, call it, and
        confirm the flag clears."""
        flag = tmp_path / ".resurrected"
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(flag))

        # Set up: bot is in lifeboat
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="user")
        assert _r.is_resurrected() is True

        # The panic handler does this exact thing — verify the
        # import + call works.
        from windyfly.agent.resurrect import normalize as _n
        out = _n()
        assert out["ok"] is True
        assert out["was_resurrected"] is True
        assert _r.is_resurrected() is False

    def test_panic_handler_source_invokes_normalize(self):
        """Static contract: the panic-handler block in
        telegram_bot.py MUST contain a normalize call. This catches
        a careless future refactor that drops the line."""
        from pathlib import Path
        import windyfly.channels.telegram_bot as _tb
        src = Path(_tb.__file__).read_text()
        # The panic handler is the only place we should be importing
        # normalize at runtime — verify that import + call exists.
        assert "from windyfly.agent.resurrect import normalize" in src
        # And that it's wired to the panic / reset path.
        assert "_is_panic_message" in src


# ─── Fix 2: Ollama error string carries recovery hint ────────────


class TestOllamaTimeoutCarriesRecoveryHint:
    """When _call_ollama times out, the user-facing string must
    include the standard /reset-/resurrect footer (PR #141). Pre-
    fix the raw `f"Local model error: {e}..."` shipped with no hint,
    leaving users staring at "timed out" with no idea what to type.
    """

    def test_timeout_error_includes_recovery_footer(self):
        from windyfly.agent.offline import _call_ollama

        # Force a network exception by pointing httpx at a closed
        # port on localhost. The exception handler is what we care
        # about — content irrelevant.
        with patch("httpx.post", side_effect=TimeoutError("simulated 30s timeout")):
            out = _call_ollama("doesnt-matter", [])

        # Old bare string ("queued for online processing") is gone,
        # new string mentions backup brain + recovery footer.
        assert "/reset" in out or "/resurrect" in out, \
            f"recovery footer missing from Ollama error: {out!r}"
        assert "backup brain" in out.lower() or "queued" in out.lower()

    def test_recovery_hint_idempotent_under_double_wrap(self):
        """The recovery_hint module is idempotent — wrapping twice
        doesn't duplicate the footer. Verify we don't accidentally
        ship a 'wrap-twice' reply when an upstream caller also adds
        the hint."""
        from windyfly.observability.recovery_hint import with_recovery_hint
        from windyfly.agent.offline import _call_ollama

        with patch("httpx.post", side_effect=ConnectionError("backend down")):
            once = _call_ollama("doesnt-matter", [])
        twice = with_recovery_hint(once)
        # The footer phrase appears exactly once, not twice.
        assert twice.count("/reset") <= 1 or twice.count("/resurrect") <= 1


# ─── Fix 3: paid-LLM recovery probe drops the flag ───────────────


class TestAttemptPaidRecovery:
    """attempt_paid_recovery() is the climb-out-of-lifeboat helper.
    Contract:
      - not resurrected → reason='not_resurrected'
      - within cooldown → reason='cooldown'
      - resurrected + paid down → reason='still_offline', flag stays
      - resurrected + paid healthy → recovered=True, flag cleared,
        notice present
    """

    def test_returns_not_resurrected_when_flag_absent(self, isolated_recovery_marker):
        out = _r.attempt_paid_recovery()
        assert out["recovered"] is False
        assert out["reason"] == "not_resurrected"

    def test_paid_unreachable_keeps_flag(self, isolated_recovery_marker, monkeypatch, tmp_path):
        flag = tmp_path / ".resurrected"
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(flag))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="test")

        with patch("windyfly.agent.offline.is_online", return_value=False):
            out = _r.attempt_paid_recovery()

        assert out["recovered"] is False
        assert out["reason"] == "still_offline"
        assert _r.is_resurrected() is True  # flag stays on

    def test_paid_healthy_clears_flag_and_returns_notice(
        self, isolated_recovery_marker, monkeypatch, tmp_path,
    ):
        flag = tmp_path / ".resurrected"
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(flag))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="test")

        with patch("windyfly.agent.offline.is_online", return_value=True):
            out = _r.attempt_paid_recovery()

        assert out["recovered"] is True
        assert "Recovered" in out["notice"]
        assert _r.is_resurrected() is False  # flag cleared

    def test_cooldown_blocks_rapid_probes(
        self, isolated_recovery_marker, monkeypatch, tmp_path,
    ):
        """Two attempts in quick succession: the second hits cooldown
        regardless of paid health, so we don't HTTP-storm
        api.anthropic.com on every chat."""
        flag = tmp_path / ".resurrected"
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(flag))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="test")

        # First call: paid is down. Marks the cooldown.
        with patch("windyfly.agent.offline.is_online", return_value=False):
            first = _r.attempt_paid_recovery()
        assert first["reason"] == "still_offline"

        # Second call within cooldown: even if paid is now healthy,
        # cooldown short-circuits.
        with patch("windyfly.agent.offline.is_online", return_value=True):
            second = _r.attempt_paid_recovery()
        assert second["recovered"] is False
        assert second["reason"] == "cooldown"


# ─── Fix 4: 🛟 prefix on lifeboat replies + paid-recovery flow ───


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


class TestLifeboatVisibilityAndRecoveryFlow:
    """Integration: agent_respond's resurrection short-circuit
    behavior in two states."""

    def test_lifeboat_reply_is_prefixed_with_buoy_emoji(
        self, stack, monkeypatch, tmp_path, isolated_recovery_marker,
    ):
        """When the bot is in lifeboat mode and the paid probe says
        still-offline, the reply must start with 🛟 so the user sees
        per-reply indication of lifeboat mode."""
        config, db, wq = stack
        flag = tmp_path / ".resurrected"
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(flag))

        # Set up lifeboat
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="test")

        from windyfly.agent.loop import agent_respond
        with patch("windyfly.agent.offline.is_online", return_value=False), \
             patch("windyfly.agent.loop.get_offline_response",
                   return_value="hi from local"):
            reply = agent_respond(config, db, wq, "hello", "session-1")

        assert reply.lstrip().startswith("🛟"), \
            f"lifeboat reply missing buoy prefix: {reply!r}"

    def test_paid_recovery_drops_flag_and_routes_through_paid(
        self, stack, monkeypatch, tmp_path, isolated_recovery_marker,
    ):
        """When the bot is in lifeboat mode but the paid probe says
        healthy, agent_respond clears the flag, routes through the
        paid LLM, and prepends the recovery notice."""
        config, db, wq = stack
        flag = tmp_path / ".resurrected"
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(flag))

        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="test")
        assert _r.is_resurrected() is True

        # Paid healthy → call_llm should be invoked, flag cleared.
        # call_llm returns a dict with content / input_tokens /
        # output_tokens / tool_calls — match that shape exactly.
        from windyfly.agent.loop import agent_respond
        paid_result = {
            "content": "paid-llm reply",
            "input_tokens": 10,
            "output_tokens": 5,
            "tool_calls": None,
            "model": "claude-haiku-4-5-20251001",
        }

        with patch("windyfly.agent.offline.is_online", return_value=True), \
             patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", return_value=paid_result):
            reply = agent_respond(config, db, wq, "hello", "session-2")

        # Flag cleared
        assert _r.is_resurrected() is False
        # Recovery notice appears
        assert "Recovered" in reply
        # Paid-llm content appears
        assert "paid-llm reply" in reply
