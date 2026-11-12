"""Reading Windy Mind's refusals, and the manners of the fallback's recovery check.

1. Mind is moving "the owner's own key was refused" off 502 (Boss RULING, BOARD 2026-11-11T03:37:10Z): through
   api.windymind.ai the edge replaces a 502's words and headers, so no agent outside Mind's own network ever saw
   ``x-mind-connection`` and the "say which key to fix, no retry" rule never fired. The agent reads the NEW form
   (a 4xx carrying ``x-mind-connection``) and the OLD one (502), so either side can ship first.
2. Every non-2xx from Mind leaves one witness line: status, Retry-After present or not, the x-mind header NAMES,
   the body length. Never a value, never content.
3. Mind's two asks for the recovery check (BOARD 2026-11-10T18:51:55Z): one check per 600 s after 15 failures in a
   row, and no check before a 429's or 503's Retry-After.
4. A recovery says which model answered, and claims "my own model is back" only when that is what answered.
"""

from __future__ import annotations

import json
import logging
import time
from unittest.mock import MagicMock, patch

import pytest

from windyfly.agent import models
from windyfly.agent import resurrect as _r


def _resp(status: int, headers: dict | None = None, body: dict | None = None, content: bytes | None = None):
    r = MagicMock()
    r.status_code = status
    r.headers = dict(headers or {})
    r.json.return_value = body if body is not None else {}
    r.text = json.dumps(body or {})
    r.content = content if content is not None else r.text.encode()
    return r


# ── 1. the key-refused reader ──

@pytest.mark.parametrize("status", [402, 424, 400, 409, 422])
def test_a_4xx_that_names_the_connection_is_the_owners_key_refused(status):
    assert models._refused_key_provider(_resp(status, {"x-mind-connection": "anthropic"})) == "Anthropic"
    assert models._refused_key_provider(_resp(status, {"x-mind-connection": "moonshot"})) == "Moonshot"


@pytest.mark.parametrize("status", [400, 402, 404, 413, 422, 424])
def test_a_4xx_without_the_header_is_an_ordinary_refusal(status):
    assert models._refused_key_provider(_resp(status)) is None
    assert models._refused_key_provider(_resp(status, {"x-mind-error": "connection"})) is None
    assert models._refused_key_provider(
        _resp(status, body={"detail": "your connected groq key was refused upstream"})) is None


@pytest.mark.parametrize("code", ["own_key_required", "budget_spent"])
def test_a_402_on_chat_is_never_a_refused_key(code):
    """mind-refusal.v1 1.17.0: 402 on /v1/chat means own_key_required or budget_spent, and x-mind-connection is
    reserved for the own-key refusal (424), so these carry no such header and stay ordinary refusals."""
    assert models._refused_key_provider(_resp(402, {"x-mind-error": code})) is None


def test_the_contracts_424_is_read_by_its_header_and_not_by_its_number():
    assert models._refused_key_provider(_resp(424, {"x-mind-connection": "groq"})) == "Groq"
    assert models._refused_key_provider(_resp(424)) is None


@pytest.mark.parametrize("status", [401, 403, 429])
def test_standing_stops_and_rate_limits_are_never_a_key_refusal(status):
    assert models._refused_key_provider(_resp(status, {"x-mind-connection": "anthropic"})) is None


def test_the_old_502_form_is_still_read():
    assert models._refused_key_provider(_resp(502, {"x-mind-connection": "groq"})) == "Groq"
    assert models._refused_key_provider(_resp(502, {"x-mind-error": "connection", "x-mind-provider": "openai"})) == "OpenAI"
    assert models._refused_key_provider(
        _resp(502, {"x-mind-error": "connection"},
              {"detail": "your connected groq key was refused upstream - check the key/billing"})) == "Groq"


def test_the_edges_bare_502_is_not_a_key_refusal():
    # What arrives through the edge today: 16 bytes, no x-mind header (Mind FINDING A).
    bare = _resp(502, {"server": "cloudflare", "content-type": "text/plain"}, content=b"error code: 502")
    assert models._refused_key_provider(bare) is None


class TestTheTurn:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-test")
        monkeypatch.setattr(models, "_provider_cooldowns", {})
        monkeypatch.setattr(models, "_mind_helper_enabled", lambda: False)

    @pytest.mark.parametrize("status", [402, 424])
    def test_the_new_form_is_sent_once_recorded_and_never_cools_mind_down(self, monkeypatch, status):
        calls, sleeps = [], []
        monkeypatch.setattr(models.time, "sleep", sleeps.append)

        def post(*a, **k):
            calls.append(1)
            return _resp(status, {"x-mind-connection": "anthropic", "retry-after": "30"})

        monkeypatch.setattr("httpx.post", post)
        out = models._try_mind_broker([{"role": "user", "content": "x"}], None, None, 100, None)
        assert out is None and models._last_mind_failure == "mind key refused:Anthropic"
        assert len(calls) == 1 and sleeps == []                 # no retry, no wait
        assert not models._is_provider_in_cooldown("windy-mind")

    def test_a_non_2xx_leaves_one_witness_line_with_names_and_sizes_only(self, monkeypatch, caplog):
        secret_words = "the owner's Anthropic key ran out of credit on 2026-11-08"
        monkeypatch.setattr("httpx.post", lambda *a, **k: _resp(
            402, {"x-mind-connection": "anthropic", "x-mind-request-id": "req-7f3a9", "Retry-After": "60",
                  "content-type": "application/json"}, {"detail": secret_words}))
        with caplog.at_level(logging.INFO, logger="windyfly.agent.models"):
            models._try_mind_broker([{"role": "user", "content": "x"}], None, None, 100, None)
        lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Mind answered")]
        assert len(lines) == 1
        line = lines[0]
        assert "402" in line and "/v1/chat" in line and "retry-after present" in line
        assert "x-mind-connection, x-mind-request-id" in line and "content-type" not in line
        assert f"body {len(json.dumps({'detail': secret_words}))} bytes" in line
        assert "req-7f3a9" not in line and "credit" not in line  # never a header value, never the body

    def test_the_edges_bare_502_is_witnessed_as_it_arrived(self, monkeypatch, caplog):
        monkeypatch.setattr(models.time, "sleep", lambda s: None)
        monkeypatch.setattr("httpx.post", lambda *a, **k: _resp(
            502, {"server": "cloudflare"}, content=b"error code: 502"))
        with caplog.at_level(logging.INFO, logger="windyfly.agent.models"):
            models._try_mind_broker([{"role": "user", "content": "x"}], None, None, 100, None)
        line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Mind answered"))
        assert "retry-after absent" in line and "x-mind headers: none" in line and "body 15 bytes" in line

    def test_a_200_leaves_no_witness_line(self, monkeypatch, caplog):
        monkeypatch.setattr("httpx.post", lambda *a, **k: _resp(200, body={"content": "hello"}))
        with caplog.at_level(logging.INFO, logger="windyfly.agent.models"):
            models._try_mind_broker([{"role": "user", "content": "x"}], None, None, 100, None)
        assert not [r for r in caplog.records if r.getMessage().startswith("Mind answered")]


# ── 3 + 4. the recovery check ──

@pytest.fixture
def on_the_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".resurrected"))
    monkeypatch.setenv("WINDY_RECOVERY_PROBE_LAST", str(tmp_path / ".recovery_probe_last"))
    monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".post_recovery_grace"))
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-abc")
    monkeypatch.setenv("MIND_API_URL", "https://mind.test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch.object(_r, "list_installed_ollama_models",
                      return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}]):
        assert _r.resurrect(actor="auto-chain-exhausted", previous_model="claude-sonnet-5-5").get("ok")
    return tmp_path


def _mind(monkeypatch, *responses):
    calls, queue = [], list(responses)

    def fake_post(url, ept, body, timeout):
        calls.append(body)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(models.mind_auth, "post", fake_post)
    return calls


def _a_check_is_due(tmp_path, seconds_ago: float) -> None:
    (tmp_path / ".recovery_probe_last").write_text(str(time.time() - seconds_ago), encoding="utf-8")


def test_the_check_sends_stream_false(on_the_fallback, monkeypatch):
    calls = _mind(monkeypatch, _resp(200))
    models.mind_can_answer("claude-sonnet-5-5")
    assert calls[0]["stream"] is False


def test_fifteen_failures_in_a_row_slow_the_check_to_one_per_ten_minutes(on_the_fallback, monkeypatch):
    calls = _mind(monkeypatch, _resp(400, body={"detail": "credit balance too low"}))
    for n in range(1, 16):
        _a_check_is_due(on_the_fallback, 121)
        assert _r.attempt_paid_recovery()["reason"] == "still_offline"
        assert _r.recovery_probe_interval() == (600.0 if n >= 15 else 120.0), n
    assert len(calls) == 15
    _a_check_is_due(on_the_fallback, 121)                       # 2 minutes later: too soon now
    assert _r.attempt_paid_recovery()["reason"] == "cooldown" and len(calls) == 15
    _a_check_is_due(on_the_fallback, 599)
    assert _r.attempt_paid_recovery()["reason"] == "cooldown" and len(calls) == 15
    _a_check_is_due(on_the_fallback, 601)
    assert _r.attempt_paid_recovery()["reason"] == "still_offline" and len(calls) == 16
    assert _r.lifeboat_status()["recovery_probe_interval_s"] == 600.0


def test_an_answer_ends_the_count_and_a_new_fallback_starts_at_two_minutes(on_the_fallback, monkeypatch):
    _mind(monkeypatch, _resp(400))
    for _ in range(15):
        _a_check_is_due(on_the_fallback, 121)
        _r.attempt_paid_recovery()
    assert _r.recovery_probe_interval() == 600.0
    _mind(monkeypatch, _resp(200, {"x-mind-model": "claude-sonnet-5-5"}))
    _a_check_is_due(on_the_fallback, 601)
    assert _r.attempt_paid_recovery()["recovered"] is True
    assert _r.recovery_probe_interval() == 120.0

    _mind(monkeypatch, _resp(400))
    for _ in range(15):
        _r._note_recovery_probe(False)
    assert _r.recovery_probe_interval() == 600.0
    with patch.object(_r, "list_installed_ollama_models",
                      return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}]):
        _r.resurrect(actor="auto-chain-exhausted", previous_model="claude-sonnet-5-5")
    assert _r.recovery_probe_interval() == 120.0                # a new fallback counts from zero


@pytest.mark.parametrize("status", [429, 503])
def test_no_check_before_minds_retry_after(on_the_fallback, monkeypatch, status):
    calls = _mind(monkeypatch, _resp(status, {"retry-after": "900"}))
    assert _r.attempt_paid_recovery()["reason"] == "still_offline"
    _a_check_is_due(on_the_fallback, 300)                       # the usual 120 s have long passed
    assert _r.attempt_paid_recovery()["reason"] == "cooldown" and len(calls) == 1
    state = _r._recovery_probe_state()
    assert 880 < state["not_before"] - time.time() <= 900


def test_retry_after_is_capped_and_only_counts_on_429_and_503(on_the_fallback, monkeypatch):
    _mind(monkeypatch, _resp(503, {"retry-after": "86400"}))
    _r.attempt_paid_recovery()
    assert _r._recovery_probe_state()["not_before"] - time.time() <= _r._RECOVERY_PROBE_RETRY_AFTER_MAX_S
    _r._note_recovery_probe(True)
    calls = _mind(monkeypatch, _resp(400, {"retry-after": "900"}))
    _a_check_is_due(on_the_fallback, 121)
    _r.attempt_paid_recovery()
    assert _r._recovery_probe_state()["not_before"] == 0
    _a_check_is_due(on_the_fallback, 121)
    assert _r.attempt_paid_recovery()["reason"] == "still_offline" and len(calls) == 2


def test_the_recovery_says_which_model_answered(on_the_fallback, monkeypatch, caplog):
    _mind(monkeypatch, _resp(200, {"x-mind-model": "claude-sonnet-5-5"}))
    with caplog.at_level(logging.INFO, logger="windyfly.agent.resurrect"):
        out = _r.attempt_paid_recovery()
    assert out["recovered"] is True and out["own_model"] is True and out["served_model"] == "claude-sonnet-5-5"
    assert out["notice"].startswith("✅ Recovered — windy-mind is healthy again")
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("RECOVERED"))
    assert "asked=claude-sonnet-5-5" in line and "served=claude-sonnet-5-5" in line and "own_model=True" in line


@pytest.mark.parametrize("headers", [
    {"x-mind-model": "llama-3.3-70b", "x-mind-fellback-from": "claude-sonnet-5-5"},      # Mind fell back
    {"x-mind-model": "llama-3.3-70b", "x-mind-served-reason": "quota_exhausted",         # Mind swapped and says why
     "x-mind-requested-model": "claude-sonnet-5-5"},
    {"x-mind-model": "claude-sonnet-5-5", "x-mind-fellback-from": "claude-opus-5-5"},     # a fallback landed on it
])
def test_another_model_answering_is_never_called_my_own_model_is_back(on_the_fallback, monkeypatch, headers):
    _mind(monkeypatch, _resp(200, headers))
    out = _r.attempt_paid_recovery()
    assert out["recovered"] is True and out["own_model"] is False       # Mind answers again: the fallback ends
    assert "is healthy again" not in out["notice"]
    assert out["notice"].startswith(f"✅ Windy Mind is answering again, with {headers['x-mind-model']}")
    assert " for now. " in out["notice"]
    if headers["x-mind-model"] != "claude-sonnet-5-5":
        assert "instead of claude-sonnet-5-5" in out["notice"]


def test_minds_spelling_of_the_same_name_is_the_agents_own_model(on_the_fallback, monkeypatch):
    """Mind matches names without regard to case and answers with its catalog spelling (Mind, BOARD
    2026-11-11T04:18:22Z (3)): that is the same model, not another one."""
    _mind(monkeypatch, _resp(200, {"x-mind-model": "Claude-Sonnet-5-5"}))
    out = _r.attempt_paid_recovery()
    assert out["recovered"] is True and out["own_model"] is True
    assert out["notice"].startswith("✅ Recovered — windy-mind is healthy again")


def test_a_swap_onto_the_same_name_in_another_spelling_names_no_instead_of(on_the_fallback, monkeypatch):
    _mind(monkeypatch, _resp(200, {"x-mind-model": "Claude-Sonnet-5-5", "x-mind-fellback-from": "claude-opus-5-5"}))
    out = _r.attempt_paid_recovery()
    assert out["own_model"] is False and "instead of" not in out["notice"] and " for now. " in out["notice"]


@pytest.mark.parametrize("served", [
    "claude-haiku-5-5",                # a declared successor serves the name the agent uses (Mind's case 1)
    "claude-sonnet-5",                 # a retired or short name mapped to its successor (case 2)
    "groq/gpt-oss-120b",
])
def test_a_different_name_alone_is_not_called_a_swap(on_the_fallback, monkeypatch, served):
    """Mind reports no swap in these cases: neither x-mind-fellback-from nor x-mind-served-reason comes. The agent
    then names the model that answered and claims nothing more: no "instead of", no "for now", no "my own model
    is back"."""
    _mind(monkeypatch, _resp(200, {"x-mind-model": served}))
    out = _r.attempt_paid_recovery()
    assert out["recovered"] is True and out["own_model"] is False
    assert out["notice"].startswith(f"✅ Windy Mind is answering again, with {served}. Switching back")
    assert "instead of" not in out["notice"] and "for now" not in out["notice"]
    assert "is healthy again" not in out["notice"]


def test_the_model_name_shown_to_the_owner_is_capped_and_has_no_control_characters(on_the_fallback, monkeypatch):
    _mind(monkeypatch, _resp(200, {"x-mind-model": "evil\x1b[31m\x00name" + "x" * 300,
                                   "x-mind-fellback-from": "a\x07b"}))
    out = _r.attempt_paid_recovery()
    assert out["served_model"] == ("evil[31mname" + "x" * 300)[:80] and len(out["served_model"]) == 80
    assert all(ch.isprintable() or ch == "\n" for ch in out["notice"])
    assert len(out["notice"]) < 300


def test_a_recovery_with_no_model_header_keeps_the_old_sentence(on_the_fallback, monkeypatch):
    _mind(monkeypatch, _resp(200))
    out = _r.attempt_paid_recovery()
    assert out["recovered"] is True and out["own_model"] is False and out["served_model"] is None
    assert out["notice"].startswith("✅ Recovered — windy-mind is healthy again")


def test_when_the_broker_picked_the_model_nothing_is_claimed(on_the_fallback, monkeypatch):
    calls, queue = [], [_resp(422), _resp(200, {"x-mind-model": "llama-3.3-70b"})]
    monkeypatch.setattr(models.mind_auth, "post", lambda url, ept, body, timeout: (calls.append(body), queue.pop(0))[1])
    out = models.mind_can_answer("a-name-mind-does-not-know")
    assert out["ok"] is True and out["asked_model"] is None and out["own_model"] is False
    assert out["served_model"] == "llama-3.3-70b"
