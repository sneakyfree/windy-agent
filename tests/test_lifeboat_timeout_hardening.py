"""Lifeboat timeout hardening (post-2026-05-20 screenshot).

Pins the contract for the fixes shipped after Grant's Telegram
screenshot showed:

  - Bot rate-limited on Opus 4.7 (Max plan)
  - auto_resurrect fired and wrote the flag (notification displayed)
  - First Ollama call timed out → user got "timed out talking to my
    backup brain" on every chat with no way out except /normal

Fixes covered here:

  1. Default Ollama timeout bumped 30s → 180s (CPU-only inference
     of 3B model on commodity hardware exceeds 30s for any
     non-trivial prompt; see _DEFAULT_OLLAMA_TIMEOUT_S docstring)
  2. WINDY_OLLAMA_TIMEOUT_S env override honored
  3. Context aggressively trimmed (5 msgs * unbounded → 3 msgs *
     400 chars) so prompt-eval latency stays bounded
  4. Distinct error message for timeout vs. other failures
  5. warm_ollama_model() called on successful resurrect (skippable
     via WINDY_SKIP_OLLAMA_WARMUP for tests)
  6. Consecutive-Ollama-failure counter tracks success/failure
  7. should_escape_lifeboat() returns True after 3 failures
  8. agent_respond escapes wedged lifeboat: clears resurrect flag,
     falls through to paid path with notice prepended
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from windyfly.agent import offline
from windyfly.agent import resurrect as _r


@pytest.fixture(autouse=True)
def isolated_flags(monkeypatch, tmp_path):
    """Per-test isolation for all lifeboat-related flags."""
    monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".resurrected"))
    monkeypatch.setenv("WINDY_AUTO_RESURRECT_DISABLED", str(tmp_path / ".auto_disabled"))
    monkeypatch.setenv("WINDY_AUTO_RESURRECT_LAST", str(tmp_path / ".auto_last"))
    monkeypatch.setenv("WINDY_RECOVERY_PROBE_LAST", str(tmp_path / ".recov_last"))
    monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".grace"))
    monkeypatch.setenv("WINDY_OLLAMA_FAILURE_COUNTER", str(tmp_path / ".ollama_fail"))
    # Point the module-level counter path at the per-test path too.
    monkeypatch.setattr(offline, "_OLLAMA_FAILURE_COUNTER_PATH", tmp_path / ".ollama_fail")
    monkeypatch.setenv("WINDY_SKIP_OLLAMA_WARMUP", "1")
    yield tmp_path


# ── Context truncation ────────────────────────────────────────────


def test_truncate_drops_oldest_messages_beyond_three():
    msgs = [
        {"role": "user", "content": f"msg{i}"} for i in range(10)
    ]
    out = offline._truncate_offline_context(msgs)
    assert len(out) == 3
    # Last 3 win.
    assert [m["content"] for m in out] == ["msg7", "msg8", "msg9"]


def test_truncate_caps_per_message_chars():
    msgs = [{"role": "user", "content": "x" * 5000}]
    out = offline._truncate_offline_context(msgs)
    assert len(out) == 1
    assert len(out[0]["content"]) == 400


def test_truncate_handles_none_and_empty():
    assert offline._truncate_offline_context(None) == []
    assert offline._truncate_offline_context([]) == []


def test_truncate_preserves_role():
    msgs = [
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "b"},
    ]
    out = offline._truncate_offline_context(msgs)
    assert out[0]["role"] == "assistant"
    assert out[1]["role"] == "user"


# ── Timeout env override ──────────────────────────────────────────


def test_default_timeout_is_180s(monkeypatch):
    monkeypatch.delenv("WINDY_OLLAMA_TIMEOUT_S", raising=False)
    assert offline._ollama_timeout_s() == 180.0


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("WINDY_OLLAMA_TIMEOUT_S", "45")
    assert offline._ollama_timeout_s() == 45.0


def test_timeout_env_garbage_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("WINDY_OLLAMA_TIMEOUT_S", "not-a-number")
    assert offline._ollama_timeout_s() == 180.0


def test_timeout_env_minimum_one_second(monkeypatch):
    # We allow tiny values (mostly for tests) but never zero/negative.
    monkeypatch.setenv("WINDY_OLLAMA_TIMEOUT_S", "0")
    assert offline._ollama_timeout_s() == 1.0


# ── Distinct error messages ───────────────────────────────────────


def test_timeout_error_mentions_cpu_inference_and_normal():
    """When Ollama times out, the user should see actionable
    guidance — not the old generic 'timed out talking to my backup
    brain' that left them stuck."""
    with patch.object(
        offline.httpx if False else httpx,  # noqa: SIM222 — import isolation
        "post",
    ):
        pass
    # Direct call with monkeypatched httpx.post
    def boom(*_a, **_kw):
        raise httpx.TimeoutException("simulated CPU stall")

    with patch.object(offline, "_pick_offline_model", return_value="llama3.2:3b"), \
         patch("httpx.post", side_effect=boom):
        out = offline._call_ollama("good morning")

    assert "/normal" in out
    assert "CPU" in out or "shorter" in out


def test_non_timeout_error_uses_distinct_wording():
    def boom(*_a, **_kw):
        raise httpx.ConnectError("simulated connect refused")

    with patch.object(offline, "_pick_offline_model", return_value="llama3.2:3b"), \
         patch("httpx.post", side_effect=boom):
        out = offline._call_ollama("hi")

    assert "queued" in out.lower()
    assert "ConnectError" in out


def test_empty_ollama_response_is_treated_as_failure():
    """Ollama can 200-OK with an empty content field if the model
    just spat out the stop token immediately. That's a failure for
    our purposes — we have nothing to send to the user."""
    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"message": {"content": "   "}}

    with patch.object(offline, "_pick_offline_model", return_value="llama3.2:3b"), \
         patch("httpx.post", return_value=Resp()):
        out = offline._call_ollama("hi")

    # Empty content path goes through the failure branch
    assert "queued" in out.lower() or "shorter" in out
    # And counter should have incremented
    assert offline.consecutive_ollama_failures() == 1


# ── Consecutive failure counter ────────────────────────────────────


def test_failure_counter_increments_on_failure():
    assert offline.consecutive_ollama_failures() == 0
    offline._record_ollama_outcome(success=False)
    assert offline.consecutive_ollama_failures() == 1
    offline._record_ollama_outcome(success=False)
    assert offline.consecutive_ollama_failures() == 2


def test_failure_counter_resets_on_success():
    offline._record_ollama_outcome(success=False)
    offline._record_ollama_outcome(success=False)
    assert offline.consecutive_ollama_failures() == 2
    offline._record_ollama_outcome(success=True)
    assert offline.consecutive_ollama_failures() == 0


def test_should_escape_only_after_three(isolated_flags):
    for _ in range(2):
        offline._record_ollama_outcome(success=False)
    assert offline.should_escape_lifeboat() is False
    offline._record_ollama_outcome(success=False)
    assert offline.should_escape_lifeboat() is True


def test_should_escape_resets_after_success():
    for _ in range(3):
        offline._record_ollama_outcome(success=False)
    assert offline.should_escape_lifeboat() is True
    offline._record_ollama_outcome(success=True)
    assert offline.should_escape_lifeboat() is False


# ── warm_ollama_model is fired on resurrect ────────────────────────


def test_warmup_called_when_resurrect_succeeds(monkeypatch):
    """A successful resurrect should pre-load the model so the
    user's first chat doesn't pay the cold-start cost."""
    monkeypatch.delenv("WINDY_SKIP_OLLAMA_WARMUP", raising=False)
    warm_calls: list[str | None] = []

    def fake_warm(model=None):
        warm_calls.append(model)
        return True

    with patch.object(
        _r, "list_installed_ollama_models",
        return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}],
    ), patch("windyfly.agent.offline.warm_ollama_model", side_effect=fake_warm):
        out = _r.resurrect(actor="test")

    assert out["ok"] is True
    assert warm_calls == ["llama3.2:3b"]


def test_warmup_skipped_when_env_flag_set(monkeypatch):
    """Test suite sets WINDY_SKIP_OLLAMA_WARMUP=1 so we don't hit a
    real Ollama. Verify that path is honored."""
    monkeypatch.setenv("WINDY_SKIP_OLLAMA_WARMUP", "1")

    warm_calls: list[str | None] = []
    def fake_warm(model=None):
        warm_calls.append(model)
        return True

    with patch.object(
        _r, "list_installed_ollama_models",
        return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}],
    ), patch("windyfly.agent.offline.warm_ollama_model", side_effect=fake_warm):
        out = _r.resurrect(actor="test")

    assert out["ok"] is True
    assert warm_calls == []


def test_warmup_failure_does_not_break_resurrect(monkeypatch):
    """If the warmup itself errors, the flag should still be
    written — we don't want a flaky warmup to lock the user out of
    lifeboat."""
    monkeypatch.delenv("WINDY_SKIP_OLLAMA_WARMUP", raising=False)

    def angry_warm(model=None):
        raise RuntimeError("ollama is grumpy")

    with patch.object(
        _r, "list_installed_ollama_models",
        return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}],
    ), patch("windyfly.agent.offline.warm_ollama_model", side_effect=angry_warm):
        out = _r.resurrect(actor="test")

    assert out["ok"] is True
    assert _r.is_resurrected() is True


# ── Wedged-lifeboat escape integration ────────────────────────────


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


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_wedged_lifeboat_escapes_after_three_failures(
    mock_llm, _online, stack, isolated_flags,
):
    """In lifeboat with 3 consecutive Ollama failures recorded, the
    next agent_respond should clear the resurrect flag, retry the
    paid path, and prepend the wedged-escape notice."""
    config, db, wq = stack
    from windyfly.agent.loop import agent_respond

    # Force lifeboat ON
    with patch.object(_r, "list_installed_ollama_models",
                      return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}]):
        _r.resurrect(actor="test")
    assert _r.is_resurrected() is True

    # Record 3 failures
    for _ in range(3):
        offline._record_ollama_outcome(success=False)
    assert offline.should_escape_lifeboat() is True

    # Block the paid-recovery probe so we don't take the "✅ Recovered"
    # branch — we want to test the wedged-escape branch.
    mock_llm.return_value = {
        "content": "hello back",
        "input_tokens": 10,
        "output_tokens": 5,
        "cost": 0.0,
        "tool_calls": None,
    }

    with patch.object(_r, "attempt_paid_recovery",
                      return_value={"recovered": False, "reason": "still_offline"}):
        response = agent_respond(config, db, wq, "hi", "test-wedged")

    # Lifeboat flag cleared
    assert _r.is_resurrected() is False
    # Wedged-escape notice prepended
    assert "wasn't keeping up" in response
    assert "switched back" in response
    # And the actual paid reply present
    assert "hello back" in response


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_lifeboat_stays_when_failures_below_threshold(
    mock_llm, _online, stack, isolated_flags,
):
    """Two consecutive failures should NOT trigger escape — the
    threshold is 3."""
    config, db, wq = stack
    from windyfly.agent.loop import agent_respond

    with patch.object(_r, "list_installed_ollama_models",
                      return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}]):
        _r.resurrect(actor="test")

    for _ in range(2):
        offline._record_ollama_outcome(success=False)

    # Ollama probe returns a normal reply; paid probe stays "still offline"
    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"message": {"content": "local reply ok"}}

    with patch.object(_r, "attempt_paid_recovery",
                      return_value={"recovered": False, "reason": "still_offline"}), \
         patch("windyfly.agent.offline.is_ollama_available", return_value=True), \
         patch("httpx.post", return_value=Resp()):
        response = agent_respond(config, db, wq, "hi", "test-no-escape")

    # Lifeboat still ON
    assert _r.is_resurrected() is True
    # No wedged-escape notice
    assert "wasn't keeping up" not in response
    # Local reply forwarded (with 🛟 prefix)
    assert "local reply ok" in response
