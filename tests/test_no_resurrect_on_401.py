"""Don't auto-resurrect on permanent 401 — PR queued from the
2026-05-20 OAuth-expired finding.

Auto-resurrect is for TRANSIENT failures (rate limits, 5xx, network
blips). Permanent auth failures (401 invalid x-api-key from a
dead/expired token) shouldn't wedge into lifeboat — the bot would
just thrash because every escape attempt 401s again.

Tests pin:
  - is_permanent_auth_error classifier (401+authentication_error,
    403+permission_error, credit-balance-too-low, etc.)
  - is_permanent_auth_error rejects transient errors (429, 5xx,
    network) and ambiguous 401s without auth markers
  - auto_resurrect_attempt returns {ok: False, reason:
    "permanent_auth_failure"} when error_str matches
  - agent_respond's chain-exhaustion path surfaces a dedicated
    "your API key looks invalid" reply on permanent_auth_failure
    (instead of falling through to offline_response)
  - The dedicated reply explicitly says "NOT auto-switching to
    local backup" so the user understands why no resurrect
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from windyfly.agent import resurrect as _r


# ── Classifier ───────────────────────────────────────────────────


class TestIsPermanentAuthError:

    def test_anthropic_401_invalid_x_api_key(self):
        msg = ("LLM call failed across all providers in chain "
               "(attempted=['anthropic']): Error code: 401 - "
               "{'type': 'error', 'error': {'type': "
               "'authentication_error', 'message': "
               "'invalid x-api-key'}}")
        assert _r.is_permanent_auth_error(msg) is True

    def test_403_permission_error(self):
        msg = "403 - {'type': 'permission_error', 'message': 'org disabled'}"
        assert _r.is_permanent_auth_error(msg) is True

    def test_credit_balance_too_low(self):
        # Anthropic uses 400 for credit-balance, but the marker is
        # in the message body. We accept 401/403 + the marker.
        msg = "401 - {'message': 'Your credit balance is too low'}"
        assert _r.is_permanent_auth_error(msg) is True

    def test_429_rate_limit_is_transient(self):
        msg = "429 - {'message': 'rate limit exceeded'}"
        assert _r.is_permanent_auth_error(msg) is False

    def test_500_5xx_is_transient(self):
        msg = "500 - internal server error"
        assert _r.is_permanent_auth_error(msg) is False

    def test_network_error_is_transient(self):
        msg = "ConnectError: connection refused"
        assert _r.is_permanent_auth_error(msg) is False

    def test_ambiguous_401_without_auth_marker_is_transient(self):
        """A bare '401' without one of our auth markers might be
        from a flaky intermediary, not the credential itself.
        Default to transient — safer to lifeboat than to leave
        the user stranded."""
        msg = "401 from proxy"
        assert _r.is_permanent_auth_error(msg) is False

    def test_auth_marker_alone_is_transient(self):
        """authentication_error without the 401 status is unusual
        and ambiguous — default to transient."""
        msg = "authentication_error during transient lookup"
        assert _r.is_permanent_auth_error(msg) is False

    def test_none_and_empty_safe(self):
        assert _r.is_permanent_auth_error(None) is False
        assert _r.is_permanent_auth_error("") is False


# ── auto_resurrect_attempt with error_str ────────────────────────


@pytest.fixture(autouse=True)
def isolated_flags(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".resurrected"))
    monkeypatch.setenv("WINDY_AUTO_RESURRECT_DISABLED", str(tmp_path / ".auto_disabled"))
    monkeypatch.setenv("WINDY_AUTO_RESURRECT_LAST", str(tmp_path / ".auto_last"))
    monkeypatch.setenv("WINDY_RECOVERY_PROBE_LAST", str(tmp_path / ".recov_last"))
    monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".grace"))
    monkeypatch.setenv("WINDY_OLLAMA_FAILURE_COUNTER", str(tmp_path / ".ollama_fail"))
    monkeypatch.setenv("WINDY_SKIP_OLLAMA_WARMUP", "1")
    yield


def test_attempt_skips_on_permanent_auth_failure():
    """The 401-invalid-x-api-key path should NOT write the
    resurrection flag and should NOT probe Ollama. Returns reason
    'permanent_auth_failure' so the caller can surface a
    dedicated reply instead of falling to offline_response."""
    err = ("Error code: 401 - {'type': 'error', 'error': "
           "{'type': 'authentication_error', 'message': "
           "'invalid x-api-key'}}")
    with patch.object(_r, "list_installed_ollama_models",
                      side_effect=AssertionError("Ollama probe should not run")):
        out = _r.auto_resurrect_attempt(
            actor="test", previous_model="claude-haiku-4-5",
            error_str=err,
        )
    assert out["ok"] is False
    assert out["reason"] == "permanent_auth_failure"
    assert _r.is_resurrected() is False


def test_attempt_proceeds_on_transient_error():
    """Rate-limit / 5xx / network errors should still trigger
    auto-resurrect — those are exactly what lifeboat is for."""
    transient_msgs = [
        "Error code: 429 - rate_limit_exceeded",
        "Error code: 503 - service_unavailable",
        "ConnectError: temporary network issue",
    ]
    for err in transient_msgs:
        with patch.object(_r, "list_installed_ollama_models",
                          return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}]):
            out = _r.auto_resurrect_attempt(
                actor="test", previous_model="claude-haiku-4-5",
                error_str=err,
            )
        assert out.get("ok") is True, f"transient err should resurrect: {err}"
        # Clean up for next iteration
        _r.normalize()
        # Clear the auto-cooldown marker
        import os
        cdpath = os.environ["WINDY_AUTO_RESURRECT_LAST"]
        try:
            os.unlink(cdpath)
        except OSError:
            pass


def test_attempt_proceeds_when_error_str_not_provided():
    """Back-compat: pre-PR callers don't pass error_str. They
    should still get the old resurrect behavior."""
    with patch.object(_r, "list_installed_ollama_models",
                      return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}]):
        out = _r.auto_resurrect_attempt(actor="test")
    assert out["ok"] is True


# ── agent_respond integration ────────────────────────────────────


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
    wq = WriteQueue()
    wq.start()
    yield _make_config(), db, wq
    try:
        wq.stop()
    except Exception:
        pass
    db.close()


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_agent_respond_surfaces_dedicated_auth_reply_on_401(
    mock_llm, _online, stack, isolated_flags,
):
    """The big one: chain exhaustion on 401-invalid-x-api-key
    should produce the dedicated 'your API key looks invalid'
    reply, NOT the lifeboat notification + offline message."""
    config, db, wq = stack
    from windyfly.agent.loop import agent_respond

    mock_llm.side_effect = RuntimeError(
        "LLM call failed across all providers in chain "
        "(attempted=['anthropic(claude-haiku-4-5-20251001)']): "
        "Error code: 401 - {'type': 'error', 'error': "
        "{'type': 'authentication_error', 'message': "
        "'invalid x-api-key'}}"
    )

    # Ollama should NOT be probed on this path — if the test's
    # Ollama probe runs, the permanent-auth short-circuit is broken
    with patch("windyfly.agent.resurrect.list_installed_ollama_models",
               side_effect=AssertionError("Ollama probe should not run on permanent auth failure")):
        reply = agent_respond(config, db, wq, "what's the weather?", "auth-test")

    # Dedicated auth-failure reply, NOT the rate-limit notification
    assert "API key looks invalid" in reply
    assert "401 invalid x-api-key" in reply
    assert "OAuth Max token expired" in reply
    # And the explicit user signal that we're NOT lifeboating
    assert "NOT auto-switching to the local backup" in reply
    # The standard rate-limit notification should NOT be present
    assert "hit a rate limit" not in reply
    # And the resurrect flag should NOT be set
    assert _r.is_resurrected() is False


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_agent_respond_still_resurrects_on_transient_429(
    mock_llm, _online, stack, isolated_flags,
):
    """Transient rate-limit chain-fail still triggers the
    standard lifeboat path. Don't regress."""
    config, db, wq = stack
    from windyfly.agent.loop import agent_respond

    mock_llm.side_effect = RuntimeError(
        "LLM call failed across all providers in chain "
        "(attempted=['anthropic(claude-haiku-4-5-20251001)']): "
        "Error code: 429 - rate_limit_exceeded"
    )
    with patch("windyfly.agent.resurrect.list_installed_ollama_models",
               return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}]), \
         patch("windyfly.agent.offline.is_ollama_available", return_value=True), \
         patch("windyfly.agent.offline._call_ollama",
               return_value="local reply"):
        reply = agent_respond(config, db, wq, "hi", "rate-test")

    assert "hit a rate limit" in reply.lower() or "auto-switched" in reply.lower()
    assert "local reply" in reply


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_auth_reply_band_gated_for_strangers(
    mock_llm, _online, stack, isolated_flags,
):
    """A SANDBOX-band sender must get the honest outage WITHOUT the
    operator runbook — env-file paths, restart instructions, and the
    recovery-hint commands are owner-band information."""
    config, db, wq = stack
    from windyfly.agent.capabilities import Band
    from windyfly.agent.loop import agent_respond

    mock_llm.side_effect = RuntimeError(
        "LLM call failed across all providers in chain "
        "(attempted=['anthropic(claude-haiku-4-5-20251001)']): "
        "Error code: 401 - {'type': 'error', 'error': "
        "{'type': 'authentication_error', 'message': "
        "'invalid x-api-key'}}"
    )
    reply = agent_respond(
        config, db, wq, "hello?", "auth-band-test", band=Band.SANDBOX,
    )

    # Honest about the outage…
    assert "credentials" in reply
    assert "operator" in reply
    # …but no operator-facing surface
    assert "~/.windy" not in reply
    assert "env file" not in reply
    assert "restart" not in reply.lower()
    assert "/reset" not in reply
    assert "/resurrect" not in reply


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_auth_dead_grandma_gets_local_floor(
    mock_llm, _online, stack, isolated_flags,
):
    """2026-07-17 honey-badger fix: a USER/SANDBOX-band sender can't fix
    a token, so 'try again later' is a strand. When the local floor
    (Ollama) is up, keep answering on it with an honest one-liner —
    per-turn only, no lifeboat latch written."""
    config, db, wq = stack
    from windyfly.agent.capabilities import Band
    from windyfly.agent.loop import agent_respond

    mock_llm.side_effect = RuntimeError(
        "LLM call failed across all providers in chain "
        "(attempted=['anthropic(claude-haiku-4-5-20251001)']): "
        "Error code: 401 - {'type': 'error', 'error': "
        "{'type': 'authentication_error', 'message': "
        "'invalid x-api-key'}}"
    )
    with patch(
        "windyfly.agent.offline.is_ollama_available", return_value=True,
    ), patch(
        "windyfly.agent.offline.get_offline_response",
        return_value="Here's my best local answer.",
    ) as mock_floor:
        reply = agent_respond(
            config, db, wq, "hello?", "auth-floor-test", band=Band.SANDBOX,
        )

    # She still gets an ANSWER…
    assert "Here's my best local answer." in reply
    mock_floor.assert_called_once()
    # …with the honest degraded-mode marker…
    assert "🛟" in reply
    assert "operator" in reply
    # …and still no operator runbook.
    assert "~/.windy" not in reply
    assert "env file" not in reply
    # No durable lifeboat latch was written (per-turn floor only).
    from windyfly.agent.resurrect import is_resurrected
    assert is_resurrected() is False


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_auth_dead_grandma_no_floor_keeps_outage_message(
    mock_llm, _online, stack, isolated_flags,
):
    """Floor down + auth dead → the previous honest outage message,
    unchanged."""
    config, db, wq = stack
    from windyfly.agent.capabilities import Band
    from windyfly.agent.loop import agent_respond

    mock_llm.side_effect = RuntimeError(
        "LLM call failed across all providers in chain "
        "(attempted=['anthropic(claude-haiku-4-5-20251001)']): "
        "Error code: 401 - {'type': 'error', 'error': "
        "{'type': 'authentication_error', 'message': "
        "'invalid x-api-key'}}"
    )
    with patch(
        "windyfly.agent.offline.is_ollama_available", return_value=False,
    ):
        reply = agent_respond(
            config, db, wq, "hello?", "auth-nofloor-test", band=Band.SANDBOX,
        )

    assert "try again later" in reply.lower()
    assert "credentials" in reply
