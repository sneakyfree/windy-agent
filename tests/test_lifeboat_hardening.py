"""Lifeboat hardening regression suite (post-2026-05-10 stuck-state).

After the four-fix bundle in PR #160 made lifeboat *recoverable*,
this suite hardens it against four follow-on architectural risks:

  Risk 1 — Reachability ≠ key validity. ``is_online()`` does an
           unauthenticated HTTP GET to api.anthropic.com; Anthropic
           returns 401 ("no key sent") which the probe counts as
           "reachable". A bot with a REVOKED key would recover on
           every probe and immediately re-fail. Fix:
           ``_paid_health_probe()`` sends the actual API key and
           only treats 2xx as healthy.

  Risk 2 — Recovery → fail → auto-resurrect ping-pong. When recovery
           succeeds and the very next paid call fails (transient
           flap), the chain-exhaust catch fires
           ``auto_resurrect_attempt`` whose 60s cooldown was
           satisfied long ago, so it bounces back into lifeboat on
           the SAME turn. Fix: 5-minute post-recovery grace window
           during which auto_resurrect_attempt short-circuits with
           reason="post_recovery_grace".

  Risk 3 — No user-facing visibility. Fix: ``/lifeboat`` slash
           command returns formatted ``lifeboat_status()`` so a
           confused user can see "am I in lifeboat? when's the
           next probe? is auto-resurrect on?".

  Risk 4 — No structured state-change events. Fix: ``log_event``
           rows for ``lifeboat.exited`` and ``lifeboat.recovery_failed``
           join the existing ``auto_resurrect.fired`` /
           ``auto_resurrect.skipped`` so dwell time + probe
           outcomes are dashboardable retroactively.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from windyfly.agent import resurrect as _r


# ─── Fix 1: paid-health probe (key validity, not reachability) ───


class TestPaidHealthProbe:

    def test_returns_no_keys_when_env_empty(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        out = _r._paid_health_probe()
        assert out["ok"] is False
        assert out["reason"] == "no_keys_configured"

    def test_anthropic_2xx_returns_ok(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("httpx.get") as mock_get:
            resp = MagicMock(status_code=200)
            mock_get.return_value = resp
            out = _r._paid_health_probe()
        assert out["ok"] is True
        assert out["provider"] == "anthropic"
        assert out["status"] == 200

    def test_anthropic_401_falls_through_to_openai(self, monkeypatch):
        """Anthropic 401 (key dead) must NOT count as healthy.
        Probe must try OpenAI next; if that's healthy, ok=True."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-revoked")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-good")
        responses = [
            MagicMock(status_code=401),  # anthropic dead
            MagicMock(status_code=200),  # openai alive
        ]
        with patch("httpx.get", side_effect=responses):
            out = _r._paid_health_probe()
        assert out["ok"] is True
        assert out["provider"] == "openai"

    def test_all_keys_dead_returns_failure(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-revoked")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-revoked")
        responses = [
            MagicMock(status_code=401),
            MagicMock(status_code=403),
        ]
        with patch("httpx.get", side_effect=responses):
            out = _r._paid_health_probe()
        assert out["ok"] is False
        assert out["reason"] == "all_keys_failed"
        assert out["last_status"] in (401, 403)

    def test_5xx_retries_once_then_falls_through(self, monkeypatch):
        """5xx is transient — retry once on the same provider, then
        try the next provider."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        responses = [
            MagicMock(status_code=503),  # first try
            MagicMock(status_code=200),  # second try succeeds
        ]
        with patch("httpx.get", side_effect=responses):
            out = _r._paid_health_probe()
        assert out["ok"] is True
        assert out["provider"] == "anthropic"

    def test_timeout_treated_as_transient(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("httpx.get", side_effect=TimeoutError("timeout")):
            out = _r._paid_health_probe()
        assert out["ok"] is False
        assert out["reason"] == "all_keys_failed"

    def test_attempt_paid_recovery_uses_real_probe(
        self, monkeypatch, tmp_path,
    ):
        """attempt_paid_recovery's recovery decision must come from
        the new probe, not the old reachability check. Verify by
        making the probe say ok=True and confirming the flag clears
        — even with no env keys (probe is mocked)."""
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        monkeypatch.setenv("WINDY_RECOVERY_PROBE_LAST", str(tmp_path / ".rp"))
        monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="test")

        with patch.object(_r, "_paid_health_probe",
                          return_value={"ok": True, "provider": "anthropic",
                                        "status": 200}):
            out = _r.attempt_paid_recovery()
        assert out["recovered"] is True
        assert out["provider"] == "anthropic"
        assert _r.is_resurrected() is False

    def test_attempt_paid_recovery_carries_probe_detail_on_failure(
        self, monkeypatch, tmp_path,
    ):
        """When the probe fails, the failure dict carries the probe
        detail so log_event can record WHY (key revoked vs. transient
        503 vs. timeout)."""
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        monkeypatch.setenv("WINDY_RECOVERY_PROBE_LAST", str(tmp_path / ".rp"))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="test")

        probe_detail = {"ok": False, "reason": "all_keys_failed",
                        "last_status": 401, "last_provider": "anthropic"}
        with patch.object(_r, "_paid_health_probe", return_value=probe_detail):
            out = _r.attempt_paid_recovery()
        assert out["recovered"] is False
        assert out["reason"] == "still_offline"
        assert out["probe"] == probe_detail


# ─── Fix 2: post-recovery grace window ───────────────────────────


class TestPostRecoveryGrace:

    def test_grace_marker_cleared_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"))
        assert _r._within_post_recovery_grace() is False

    def test_recovery_success_marks_grace(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        monkeypatch.setenv("WINDY_RECOVERY_PROBE_LAST", str(tmp_path / ".rp"))
        monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="test")
        with patch.object(_r, "_paid_health_probe",
                          return_value={"ok": True, "provider": "anthropic",
                                        "status": 200}):
            out = _r.attempt_paid_recovery()
        assert out["recovered"] is True
        # Grace marker is now stamped — auto_resurrect_attempt
        # should short-circuit if called immediately.
        assert _r._within_post_recovery_grace() is True

    def test_auto_resurrect_skipped_during_grace(self, monkeypatch, tmp_path):
        """The big anti-pingpong invariant: grace blocks
        auto_resurrect_attempt before the cooldown check."""
        monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"))
        monkeypatch.setenv("WINDY_AUTO_RESURRECT_LAST", str(tmp_path / ".al"))
        monkeypatch.setenv("WINDY_AUTO_RESURRECT_DISABLED",
                           str(tmp_path / ".ad"))

        # Stamp the grace marker by hand
        _r._mark_post_recovery()
        assert _r._within_post_recovery_grace() is True

        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            out = _r.auto_resurrect_attempt()
        assert out["ok"] is False
        assert out["reason"] == "post_recovery_grace"

    def test_auto_resurrect_works_after_grace_expires(
        self, monkeypatch, tmp_path,
    ):
        """After the grace expires, auto_resurrect_attempt resumes
        normal behavior."""
        import time
        monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"))
        monkeypatch.setenv("WINDY_AUTO_RESURRECT_LAST", str(tmp_path / ".al"))
        monkeypatch.setenv("WINDY_AUTO_RESURRECT_DISABLED",
                           str(tmp_path / ".ad"))
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))

        # Write a stale grace marker (10 min ago)
        (tmp_path / ".g").write_text(str(time.time() - 600))
        assert _r._within_post_recovery_grace() is False

        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            out = _r.auto_resurrect_attempt()
        assert out["ok"] is True


# ─── Fix 3: /lifeboat status command ─────────────────────────────


class TestLifeboatStatus:

    def test_status_when_not_resurrected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        s = _r.lifeboat_status()
        assert s["in_lifeboat"] is False
        assert s["model"] is None
        assert s["auto_resurrect_enabled"] is True

    def test_status_when_resurrected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="user", previous_model="claude-haiku")
        s = _r.lifeboat_status()
        assert s["in_lifeboat"] is True
        assert s["model"] == "llama3.2:3b"
        assert s["actor"] == "user"
        assert s["previous_model"] == "claude-haiku"

    def test_format_status_inactive_text(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        out = _r.format_lifeboat_status()
        assert "inactive" in out.lower()
        # Always show auto-resurrect line
        assert "auto-resurrect" in out.lower()
        # Help footer
        assert "/resurrect" in out
        assert "/normal" in out

    def test_format_status_active_text(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="auto-chain-exhausted",
                         previous_model="claude-haiku-4-5")
        out = _r.format_lifeboat_status()
        assert "ACTIVE" in out
        assert "llama3.2:3b" in out
        assert "auto-chain-exhausted" in out
        assert "claude-haiku-4-5" in out

    def test_format_status_during_grace_window(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"))
        _r._mark_post_recovery()
        out = _r.format_lifeboat_status()
        assert "grace" in out.lower()
        assert "ping-pong" in out.lower()

    def test_slash_parser_recognizes_aliases(self):
        from windyfly.channels.slash_commands import is_lifeboat_status_message
        assert is_lifeboat_status_message("/lifeboat") is True
        assert is_lifeboat_status_message("/LIFEBOAT") is True
        assert is_lifeboat_status_message("  /lifeboat  ") is True
        assert is_lifeboat_status_message("/lifeboat-status") is True
        assert is_lifeboat_status_message("/lifeboatstatus") is True

    def test_slash_parser_rejects_unrelated(self):
        from windyfly.channels.slash_commands import is_lifeboat_status_message
        assert is_lifeboat_status_message("/resurrect") is False
        assert is_lifeboat_status_message("/normal") is False
        assert is_lifeboat_status_message("hello") is False
        assert is_lifeboat_status_message("") is False
        assert is_lifeboat_status_message(None) is False


# ─── Fix 4: structured state-change events ───────────────────────


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
    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    wq = WriteQueue(); wq.start()
    yield _make_config(), db, wq
    try: wq.stop()
    except Exception: pass
    db.close()


class TestStateChangeEvents:

    def test_lifeboat_exited_event_fires_on_recovery(
        self, stack, monkeypatch, tmp_path,
    ):
        config, db, wq = stack
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="test")

        paid_result = {
            "content": "paid reply", "input_tokens": 10,
            "output_tokens": 5, "tool_calls": None,
            "model": "claude-haiku-4-5-20251001",
        }
        with patch.object(_r, "_paid_health_probe",
                          return_value={"ok": True, "provider": "anthropic",
                                        "status": 200}), \
             patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", return_value=paid_result), \
             patch("windyfly.agent.loop.log_event") as mock_log:
            from windyfly.agent.loop import agent_respond
            agent_respond(config, db, wq, "hi", "session-1")

        # Verify lifeboat.exited was logged
        names = [c.args[2] for c in mock_log.call_args_list]
        assert "lifeboat.exited" in names

    def test_lifeboat_recovery_failed_event_fires_when_probe_fails(
        self, stack, monkeypatch, tmp_path,
    ):
        config, db, wq = stack
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="test")

        with patch.object(_r, "_paid_health_probe",
                          return_value={"ok": False, "reason":
                                        "all_keys_failed",
                                        "last_status": 401}), \
             patch("windyfly.agent.loop.get_offline_response",
                   return_value="local reply"), \
             patch("windyfly.agent.loop.log_event") as mock_log:
            from windyfly.agent.loop import agent_respond
            agent_respond(config, db, wq, "hi", "session-2")

        names = [c.args[2] for c in mock_log.call_args_list]
        assert "lifeboat.recovery_failed" in names


# ─── Big one: end-to-end ping-pong simulation ────────────────────


class TestAntiPingPongIntegration:

    def test_recovery_then_paid_fail_does_not_re_resurrect(
        self, stack, monkeypatch, tmp_path,
    ):
        """The full end-to-end ping-pong scenario:
          1. Bot is in lifeboat (auto-resurrect fired earlier)
          2. Recovery probe says paid is healthy → flag clears
          3. The very next paid call fails (chain-exhausted)
          4. auto_resurrect_attempt fires from the chain-fail catch
          5. Without the grace fix: re-resurrects (PING-PONG)
             With the grace fix: skips with reason='post_recovery_grace'
        """
        config, db, wq = stack
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        monkeypatch.setenv("WINDY_RECOVERY_PROBE_LAST", str(tmp_path / ".rp"))
        monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"))
        monkeypatch.setenv("WINDY_AUTO_RESURRECT_LAST", str(tmp_path / ".al"))
        monkeypatch.setenv("WINDY_AUTO_RESURRECT_DISABLED",
                           str(tmp_path / ".ad"))

        # 1. Enter lifeboat
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="auto-chain-exhausted",
                         previous_model="claude-haiku-4-5")
        assert _r.is_resurrected() is True

        # 2 + 3. Probe says healthy → recovery → paid call fails
        from windyfly.agent.loop import agent_respond
        chain_fail = RuntimeError(
            "LLM call failed across all providers in chain: 401"
        )
        with patch.object(_r, "_paid_health_probe",
                          return_value={"ok": True, "provider": "anthropic",
                                        "status": 200}), \
             patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", side_effect=chain_fail), \
             patch.object(_r, "list_installed_ollama_models", return_value=[
                 {"name": "llama3.2:3b", "size": 2_000_000_000},
             ]), \
             patch("windyfly.agent.loop.get_offline_response",
                   return_value="offline-fallback"):
            reply = agent_respond(config, db, wq, "hello", "session-pp")

        # 4 + 5. Verify NO re-resurrection.
        # Recovery cleared the flag at step 1.7. Then call_llm raised
        # chain-fail at step 4. The chain-fail catch invoked
        # auto_resurrect_attempt — which MUST have short-circuited
        # via post_recovery_grace, so the resurrect flag should
        # still be CLEARED at the end of the turn.
        assert _r.is_resurrected() is False, \
            "ANTI-PINGPONG REGRESSION: paid recovery → paid fail re-resurrected the bot"
        # And the recovery notice should appear in the reply.
        assert "Recovered" in reply or "anthropic" in reply.lower() \
            or "offline-fallback" in reply
