"""Field telemetry: service.boot / service.health / agent.run_failed /
agent.model_demoted, the ingest's quarantine answer, synthetic marking,
the opt-out and the first-run disclosure.

The declared enums live at windy-admin (#290); a row outside them is
quarantined whole, so these tests pin that invalid rows never leave.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import httpx
import pytest

from windyfly.observability import admin_telemetry as at
from windyfly.observability import agent_health as ah
from windyfly.observability import disclosure, synthetic


class FakeQueue:
    """Runs each queued job immediately, like a drained write queue."""

    def __init__(self) -> None:
        self.jobs: list[tuple] = []

    def enqueue(self, _prio: Any, fn: Any, *args: Any, **kw: Any) -> None:
        self.jobs.append((fn, args))
        fn(*args, **kw)


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for k in ("WINDY_TELEMETRY", "WINDY_SYNTHETIC", "WINDY_AGENT_PASSPORT",
              "ETERNITAS_PASSPORT", "ETERNITAS_PASSPORT_TOKEN", "WINDY_PASSPORT_EPT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WINDY_ADMIN_INGEST_URL", "https://ingest.test")
    monkeypatch.setenv("WINDY_ADMIN_INGEST_TOKEN", "svc-token")
    monkeypatch.setenv("WINDY_AGENT_PASSPORT", "ET26-TEST-0001")
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
    for k in ("WINDY_TELEMETRY_CLIENT_TOKEN", "WINDY_TELEMETRY_EPT_AUTH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(at, "_passport_cache", None)
    monkeypatch.setattr(at, "_breaker_tripped", False)
    ah._reset_for_tests()
    yield
    ah._reset_for_tests()


class _Sent(list):
    """The POSTs seen, plus the reply the fake ingest gives."""

    def __init__(self) -> None:
        super().__init__()
        self.reply: dict[str, Any] = {
            "body": {"accepted": 1, "quarantined": 0, "rejections": []}, "status": 202,
        }


@pytest.fixture
def posted(monkeypatch):
    """Capture what would be POSTed; answer 202 {accepted:1, quarantined:0}."""
    sent = _Sent()
    reply = sent.reply

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        sent.append({"url": url, "json": json, "headers": headers})
        return httpx.Response(reply["status"], json=reply["body"])

    monkeypatch.setattr(httpx, "post", fake_post)
    return sent


def _rows(sent: list[dict]) -> list[dict]:
    """Flush the batcher, then every event POSTed so far."""
    at.flush()
    return [e for p in sent for e in p["json"]["events"]]


def _sent(sent: list[dict]) -> list[dict]:
    """Flush the batcher, then the POST requests so far."""
    at.flush()
    return list(sent)


# ── agent.run_failed ────────────────────────────────────────────────

def test_run_failed_row_shape(posted):
    q = FakeQueue()
    ah.emit_run_failed("rate_limited", write_queue=q, stage="llm", http_status=429,
                       channel="matrix", model="claude-opus-5", provider="anthropic",
                       duration_ms=812)
    (row,) = _rows(posted)
    assert row["event_type"] == "agent.run_failed"
    assert row["platform"] == "windy-agent" and row["service"] == "fly"
    assert row["actor_type"] == "agent" and row["actor_id"] == "ET26-TEST-0001"
    assert row["metadata"] == {"code": "rate_limited", "stage": "llm",
                               "http_status": 429, "channel": "matrix"}
    assert row["model"] == "claude-opus-5" and row["duration_ms"] == 812


def test_undeclared_code_or_stage_is_dropped_and_counted(posted):
    q = FakeQueue()
    assert ah.emit_run_failed("provider_auth", write_queue=q) is None
    assert ah.emit_run_failed("auth", write_queue=q, stage="network") is None
    assert _sent(posted) == []
    row = ah.health_row(q)
    assert row["metadata"]["telemetry_dropped"] == 2


def test_turn_emits_one_run_failed_for_a_marked_turn(posted):
    q = FakeQueue()
    with ah.turn(q):
        ah.mark_turn_failed("lifeboat", lifeboat=True, channel="telegram", model="m")
    (row,) = _rows(posted)
    assert row["metadata"]["code"] == "lifeboat"
    assert isinstance(row["duration_ms"], int)
    health = ah.health_row(q)["metadata"]
    assert health["turns"] == 1 and health["turn_errors"] == 1
    assert health["lifeboat_turns"] == 1


def test_turn_crash_is_internal_and_reraises(posted):
    q = FakeQueue()
    with pytest.raises(ValueError):
        with ah.turn(q):
            raise ValueError("boom")
    (row,) = _rows(posted)
    assert row["metadata"]["code"] == "internal"
    assert "boom" not in json.dumps(row)


def test_a_good_turn_emits_nothing_but_counts(posted):
    q = FakeQueue()
    with ah.turn(q):
        pass
    assert _sent(posted) == []
    assert ah.health_row(q)["metadata"]["turns"] == 1


def test_failure_code_classification():
    assert ah.failure_code("... attempted=[], skipped=['a(m):no-key']") == "no_provider"
    assert ah.failure_code("Error code: 429 rate_limit_error") == "rate_limited"
    assert ah.failure_code("401 invalid x-api-key") == "auth"
    assert ah.failure_code("Your credit balance is too low") == "quota_exceeded"
    assert ah.failure_code("Request timed out") == "timeout"
    assert ah.failure_code("prompt is too long for the context window") == "context_overflow"
    assert ah.failure_code("500 overloaded") == "provider_http"
    for c in ("x", "", "connection refused"):
        assert ah.failure_code(c) in ah.RUN_FAILED_CODES


# ── agent.model_demoted ─────────────────────────────────────────────

def test_demotion_emits_once_per_transition(posted):
    q = FakeQueue()
    assert ah.note_demotion("claude-opus-5", "llama3.2:3b", "credential_missing",
                            write_queue=q) is not None
    # steady demoted state: no repeat
    assert ah.note_demotion("claude-opus-5", "llama3.2:3b", "credential_missing",
                            write_queue=q) is None
    assert ah.is_demoted()
    ah.note_recovered()
    assert not ah.is_demoted()
    # recovery then a fresh demotion emits again
    assert ah.note_demotion("claude-opus-5", "llama3.2:3b", "rate_limited",
                            write_queue=q) is not None
    rows = _rows(posted)
    assert [r["metadata"]["reason"] for r in rows] == ["credential_missing", "rate_limited"]
    assert rows[0]["metadata"]["from_model"] == "claude-opus-5"
    assert rows[0]["metadata"]["to_model"] == "llama3.2:3b"


def test_demotion_reason_mapping():
    assert ah.demotion_reason(None, "anthropic(claude-opus-5):no-key") == "credential_missing"
    assert ah.demotion_reason("auth", "401") == "credential_rejected"
    assert ah.demotion_reason("rate_limited", "") == "rate_limited"
    assert ah.demotion_reason(None, "credit balance is too low") == "quota_exceeded"
    assert ah.demotion_reason("timeout", "") == "provider_unreachable"
    assert ah.demotion_reason(None, "cooldown") == "provider_unreachable"


def test_undeclared_demotion_reason_is_dropped(posted):
    assert ah.note_demotion("a", "b", "vibes", write_queue=FakeQueue()) is None
    assert _sent(posted) == []


def test_call_llm_failover_is_a_demotion_and_primary_is_recovery(monkeypatch, posted):
    """A chain call answered by a later model = model_demoted (reason from
    why the first didn't answer); the first model answering = recovered."""
    from windyfly.agent import models

    q = FakeQueue()
    monkeypatch.setattr(ah, "_write_queue", q)
    monkeypatch.setattr(models, "_max_oauth_active", lambda: True)  # skip Mind
    monkeypatch.setattr(models, "_is_provider_in_cooldown", lambda _k: False)
    monkeypatch.setattr(models, "_record_llm_call", lambda _r: None)
    keys = {"claude-opus-5": "", "llama3.2:3b": "x"}

    def provider_for(m, _cfg):
        return {"provider_key": m, "type": "openai", "api_key": keys[m],
                "base_url": "http://localhost:11434/v1" if m.startswith("llama") else "https://x"}

    monkeypatch.setattr(models, "get_provider_for_model", provider_for)
    monkeypatch.setattr(models, "_call_openai", lambda *a, **k: {
        "content": "hi", "input_tokens": 1, "output_tokens": 1})
    cfg = {"agent": {"failover_chain": ["claude-opus-5", "llama3.2:3b"]}}
    models.call_llm([{"role": "user", "content": "x"}], config=cfg)
    (row,) = _rows(posted)
    assert row["event_type"] == "agent.model_demoted"
    assert row["metadata"]["reason"] == "credential_missing"
    assert ah.is_demoted()
    keys["claude-opus-5"] = "sk-live"
    models.call_llm([{"role": "user", "content": "x"}], config=cfg)
    assert not ah.is_demoted()


def test_explicit_model_calls_never_touch_demotion_state(monkeypatch, posted):
    from windyfly.agent import models

    monkeypatch.setattr(models, "_max_oauth_active", lambda: True)
    monkeypatch.setattr(models, "_record_llm_call", lambda _r: None)
    monkeypatch.setattr(models, "get_provider_for_model", lambda m, c: {
        "provider_key": "p", "type": "openai", "api_key": "k", "base_url": "https://x"})
    monkeypatch.setattr(models, "_call_openai", lambda *a, **k: {
        "content": "hi", "input_tokens": 1, "output_tokens": 1})
    ah.note_demotion("claude-opus-5", "llama3.2:3b", "rate_limited", write_queue=FakeQueue())
    models.call_llm([{"role": "user", "content": "x"}], model="claude-haiku-4-5")
    assert ah.is_demoted()  # a helper's own model choice isn't a recovery


# ── service.health / service.boot ──────────────────────────────────

def test_health_counts_the_interval_then_resets(posted):
    q = FakeQueue()
    ah.note_tool_call(True)
    ah.note_tool_call(False)
    ah.note_llm_record({"status": "failed", "error_code": "rate_limited"})
    ah.note_llm_record({"status": "ok"})
    for ms in (100, 200):
        with ah.turn(q):
            pass
    ah.note_demotion("a", "b", "rate_limited", write_queue=q)
    m = ah.health_row(q)["metadata"]
    assert m["tool_calls"] == 2 and m["tool_errors"] == 1
    assert m["retries_429"] == 1 and m["turns"] == 2 and m["degraded"] is True
    assert m["telemetry_quarantined"] == 0 and m["telemetry_dropped"] == 0
    assert isinstance(m["interval_s"], int)
    nxt = ah.health_row(q)["metadata"]
    assert nxt["turns"] == 0 and nxt["tool_calls"] == 0


def test_p95_absent_when_no_turns_never_a_fake_zero(posted):
    m = ah.health_row(FakeQueue())["metadata"]
    assert "p95_turn_ms" not in m


def test_boot_row_once_per_process(posted, monkeypatch):
    monkeypatch.setenv("WINDY_HEALTH_INTERVAL_S", "3600")
    q = FakeQueue()
    ah.start(q, channel="matrix")
    ah.start(q, channel="matrix")
    boots = [r for r in _rows(posted) if r["event_type"] == "service.boot"]
    assert len(boots) == 1
    meta = boots[0]["metadata"]
    assert meta["install"] in ah.INSTALL_KINDS and meta["channel_count"] == 1
    assert meta["version"]


# ── ingest answer: quarantine + drops ──────────────────────────────

def test_quarantine_is_warned_and_counted(posted, caplog):
    posted.reply["body"] = {"accepted": 0, "quarantined": 1, "rejections": ["bad enum"]}
    q = FakeQueue()
    with caplog.at_level("WARNING"):
        ah.emit_run_failed("auth", write_queue=q)
        at.flush()
    assert "QUARANTINED" in caplog.text and "bad enum" in caplog.text
    posted.reply["body"] = {"accepted": 1, "quarantined": 0}
    assert ah.health_row(q)["metadata"]["telemetry_quarantined"] == 1


def test_failed_send_counts_as_dropped(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", boom)
    q = FakeQueue()
    ah.emit_run_failed("auth", write_queue=q)
    at.flush()
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(202, json={}))
    assert ah.health_row(q)["metadata"]["telemetry_dropped"] == 1


# ── opt-out, auth, privacy ─────────────────────────────────────────

def test_opt_out_sends_nothing(posted, monkeypatch):
    monkeypatch.setenv("WINDY_TELEMETRY", "0")
    q = FakeQueue()
    ah.emit_run_failed("auth", write_queue=q)
    ah.note_demotion("a", "b", "rate_limited", write_queue=q)
    assert ah.health_row(q) is None
    assert _sent(posted) == []
    assert not at.enabled()


def _ept(sub: str) -> str:
    def b(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b({'alg': 'ES256'})}.{b({'sub': sub})}.sig"


def test_token_source_order(monkeypatch):
    """fleet emitter token → WINDY_TELEMETRY_CLIENT_TOKEN → the agent's own
    EPT (default ON; WINDY_TELEMETRY_EPT_AUTH=0 turns it off) → nothing."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.delenv("WINDY_AGENT_PASSPORT")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _ept("ET26-ABCD-1234"))
    monkeypatch.setenv("WINDY_TELEMETRY_CLIENT_TOKEN", "client-tok")
    disclosure.after_hatch(lambda _l: None)  # customer paths need disclosure
    assert at._ingest_target() == ("https://ingest.test", "svc-token")  # fleet wins
    monkeypatch.delenv("WINDY_ADMIN_INGEST_TOKEN")
    monkeypatch.delenv("WINDY_ADMIN_INGEST_URL")
    assert at._ingest_target() == (at.DEFAULT_INGEST_URL, "client-tok")
    monkeypatch.delenv("WINDY_TELEMETRY_CLIENT_TOKEN")
    assert at._ingest_target() == (at.DEFAULT_INGEST_URL, _ept("ET26-ABCD-1234"))
    assert at.own_passport() == "ET26-ABCD-1234"
    monkeypatch.setenv("WINDY_TELEMETRY_EPT_AUTH", "0")
    assert at._ingest_target() is None


def test_customer_install_with_a_passport_sends_via_its_ept_and_discloses(monkeypatch):
    """The default customer path: no fleet or client token, an EPT → rows
    go out signed with the EPT, and the first-run line is shown."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.delenv("WINDY_ADMIN_INGEST_TOKEN")
    monkeypatch.delenv("WINDY_ADMIN_INGEST_URL")
    monkeypatch.delenv("WINDY_AGENT_PASSPORT")
    monkeypatch.setattr(at, "_ensure_flusher", lambda: None)
    ept = _ept("ET26-ABCD-1234")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", ept)
    seen: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        seen.append({"url": url, "json": json, "headers": headers})
        return httpx.Response(202, json={"accepted": 1, "quarantined": 0})

    monkeypatch.setattr(httpx, "post", fake_post)
    shown: list[str] = []
    assert disclosure.maybe_show(shown.append) and shown == [disclosure.LINE]
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    at.flush()
    (req,) = seen
    assert req["url"] == f"{at.DEFAULT_INGEST_URL}/v1/events"
    assert req["headers"]["Authorization"] == f"Bearer {ept}"
    assert req["json"]["events"][0]["actor_id"] == "ET26-ABCD-1234"


@pytest.mark.parametrize("how", ["no_ept", "ept_auth_off"])
def test_unconfigured_install_sends_and_counts_nothing(monkeypatch, how):
    """No fleet/client token and no usable EPT path: silence, no counts,
    and no disclosure line (there's nothing to disclose)."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.delenv("WINDY_ADMIN_INGEST_TOKEN")
    monkeypatch.delenv("WINDY_ADMIN_INGEST_URL")
    if how == "ept_auth_off":
        monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _ept("ET26-ABCD-1234"))
        monkeypatch.setenv("WINDY_TELEMETRY_EPT_AUTH", "0")
    calls: list = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append(a))
    q = FakeQueue()
    assert ah.emit_run_failed("auth", write_queue=q) is None
    assert ah.emit_run_failed("not-a-code", write_queue=q) is None
    assert ah.note_demotion("a", "b", "rate_limited", write_queue=q) is None
    assert ah.health_row(q) is None
    at.flush()
    assert calls == [] and at.pending() == []
    assert ah._iv.dropped == 0
    shown: list[str] = []
    assert not disclosure.maybe_show(shown.append) and shown == []


def test_content_like_metadata_keys_are_never_sent(posted):
    q = FakeQueue()
    assert ah._emit("service.health", {"message_count": 1}, q) is None
    assert _sent(posted) == []
    for fam in (ah.RUN_FAILED_CODES, ah.RUN_FAILED_STAGES, ah.DEMOTION_REASONS):
        assert all(isinstance(v, str) for v in fam)
    # the real rows' keys are all clean
    ah.emit_run_failed("auth", write_queue=q, stage="llm", channel="cli", attempts=2)
    ah.note_demotion("a", "b", "rate_limited", write_queue=q)
    ah.health_row(q)
    for row in _rows(posted):
        assert ah._valid_keys(row["metadata"])


# ── synthetic ─────────────────────────────────────────────────────

def test_synthetic_stamps_rows_and_adds_the_header(posted, monkeypatch):
    monkeypatch.setenv("WINDY_SYNTHETIC", "1")
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    assert _rows(posted)[0]["metadata"]["synthetic"] is True

    orig = httpx.Client.send
    try:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(200)

        assert synthetic.install()
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            c.get("https://api.windymind.ai/v1/x")
        assert seen.get("x-windy-synthetic") == "1"
    finally:
        httpx.Client.send = orig  # type: ignore[method-assign]
        synthetic._installed = False


def test_synthetic_header_only_goes_to_windy_hosts(monkeypatch):
    monkeypatch.setenv("WINDY_SYNTHETIC", "1")
    orig = httpx.Client.send
    try:
        seen: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen[request.url.host] = request.headers.get("x-windy-synthetic")
            return httpx.Response(200)

        assert synthetic.install()
        hosts = ["api.anthropic.com", "api.openai.com", "huggingface.co",
                 "windyword.ai.evil.com", "notwindymind.ai",
                 "account.windyword.ai", "api.eternitas.ai", "windycloud.com"]
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            for h in hosts:
                c.get(f"https://{h}/x")
        for h in hosts[:5]:
            assert seen[h] is None, h
        for h in hosts[5:]:
            assert seen[h] == "1", h
    finally:
        httpx.Client.send = orig  # type: ignore[method-assign]
        synthetic._installed = False


def test_real_traffic_is_never_marked(posted):
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    assert "synthetic" not in _rows(posted)[0]["metadata"]
    assert synthetic.install() is False


# ── disclosure ────────────────────────────────────────────────────

def test_disclosure_shown_once(tmp_path):
    shown: list[str] = []
    assert disclosure.maybe_show(shown.append)
    assert not disclosure.maybe_show(shown.append)
    assert shown == [disclosure.LINE]
    assert "never your messages" in disclosure.LINE and "WINDY_TELEMETRY=0" in disclosure.LINE


def test_disclosure_not_shown_when_opted_out(monkeypatch):
    monkeypatch.setenv("WINDY_TELEMETRY", "0")
    shown: list[str] = []
    assert not disclosure.maybe_show(shown.append)
    assert shown == []


# ── agent loop wiring ─────────────────────────────────────────────

def test_agent_respond_lifeboat_turn_emits_run_failed_and_demotion(monkeypatch, posted):
    from windyfly.agent import loop, offline

    q = FakeQueue()
    monkeypatch.setattr(offline, "_pick_offline_model", lambda: "llama3.2:3b")

    def fake_turn(config, db, wq, user_message, session_id, **kw):
        loop._lifeboat_telemetry("claude-opus-5", "network", "provider_unreachable",
                                 "matrix", wq)
        return "🛟 local answer"

    monkeypatch.setattr(loop, "_agent_respond_turn", fake_turn)
    assert loop.agent_respond({}, None, q, "hi", "s1") == "🛟 local answer"
    rows = {r["event_type"]: r for r in _rows(posted)}
    assert rows["agent.run_failed"]["metadata"]["code"] == "network"
    assert rows["agent.run_failed"]["metadata"]["channel"] == "matrix"
    assert rows["agent.model_demoted"]["metadata"] == {
        "from_model": "claude-opus-5", "to_model": "llama3.2:3b",
        "reason": "provider_unreachable",
    }
    assert ah.health_row(q)["metadata"]["lifeboat_turns"] == 1


def test_tool_dispatch_counts_calls_and_errors(monkeypatch):
    from windyfly.agent import loop

    results = iter(['{"ok": true}', '{"error": "Unknown tool: x"}', "plain text"])
    monkeypatch.setattr(loop, "_dispatch_tool_call_inner", lambda *a, **k: next(results))
    for _ in range(3):
        loop._dispatch_tool_call("f", {}, None, None, None, Exception)
    m = ah.health_row(FakeQueue())["metadata"]
    assert m["tool_calls"] == 3 and m["tool_errors"] == 1


# ── circuit breaker ──────────────────────────────────────────────

@pytest.mark.parametrize("status", [401, 403])
def test_refused_credentials_trip_the_breaker_once(posted, caplog, tmp_path, status):
    posted.reply["status"] = status
    posted.reply["body"] = {"detail": "Unknown service token"}
    q = FakeQueue()
    with caplog.at_level("WARNING"):
        for _ in range(5):
            ah.emit_run_failed("auth", write_queue=q)
    assert len(_sent(posted)) == 1  # one request, then silence: no retry storm
    warnings = [r for r in caplog.records
                if "refused this install's credentials" in r.getMessage()]
    assert len(warnings) == 1 and "not sending for 24h" in warnings[0].getMessage()
    marker = tmp_path / "telemetry_refused"
    assert marker.exists() and (marker.stat().st_mode & 0o777) == 0o600
    assert at.breaker_open()
    # every unsent row is counted as dropped (health row itself is dropped too)
    assert ah._iv.dropped == 5


def test_breaker_marker_survives_restart_then_expires(posted, tmp_path, monkeypatch):
    import os
    import time

    marker = tmp_path / "telemetry_refused"
    marker.write_text("x\n")
    # a "new process": nothing tripped in memory, but the marker holds
    assert at.breaker_open()
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    assert _sent(posted) == []
    # 25 hours later the marker has expired: sends resume, marker removed
    old = time.time() - 25 * 3600
    os.utime(marker, (old, old))
    assert not at.breaker_open()
    assert not marker.exists()
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    assert len(_sent(posted)) == 1


# ── batching, 429, 413, 401 refresh ─────────────────────────────────

def test_many_events_go_in_one_request(posted):
    q = FakeQueue()
    for _ in range(40):
        ah.emit_run_failed("auth", write_queue=q)
    assert len(at.pending()) == 40
    assert len(_sent(posted)) == 1
    assert len(_rows(posted)) == 40 and at.pending() == []


def test_batches_are_capped_at_100_rows(posted):
    q = FakeQueue()
    for _ in range(250):
        ah.emit_run_failed("auth", write_queue=q)
    sizes = [len(p["json"]["events"]) for p in _sent(posted)]
    assert sizes == [100, 100, 50]


def test_429_backs_off_without_the_breaker_then_resumes(posted, monkeypatch):
    import time

    posted.reply["status"] = 429
    posted.reply["body"] = {"detail": "rate limited"}
    q = FakeQueue()
    for _ in range(3):
        ah.emit_run_failed("auth", write_queue=q)
    assert len(_sent(posted)) == 1
    assert len(at.pending()) == 3            # re-queued, not dropped
    assert not at.breaker_open()
    assert at._retry_at > time.time() + 30   # default Retry-After 60 s
    at.flush()
    assert len(posted) == 1                  # still waiting: no retry storm
    posted.reply["status"] = 202
    posted.reply["body"] = {"accepted": 3, "quarantined": 0}
    monkeypatch.setattr(at, "_retry_at", 0.0)  # the wait is over
    at.flush()
    assert len(posted) == 2 and at.pending() == []
    assert ah.health_row(q) is not None and ah._iv.dropped == 0


def test_429_honours_retry_after(monkeypatch):
    import time

    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(
        429, headers={"Retry-After": "7"}, json={}))
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    at.flush()
    assert time.time() + 5 < at._retry_at < time.time() + 9


def test_buffer_is_bounded_dropping_the_oldest(posted, monkeypatch):
    monkeypatch.setattr(at, "MAX_BUFFER", 5)
    q = FakeQueue()
    for i in range(8):
        ah.emit_run_failed("auth", write_queue=q, attempts=i)
    kept = [r["metadata"]["attempts"] for r in at.pending()]
    assert kept == [3, 4, 5, 6, 7]
    assert ah._iv.dropped == 3


def test_413_splits_and_resends_with_a_warning(monkeypatch, caplog):
    sizes: list[int] = []

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        n = len(json["events"])
        sizes.append(n)
        return httpx.Response(413 if n > 2 else 202, json={"accepted": n, "quarantined": 0})

    monkeypatch.setattr(httpx, "post", fake_post)
    q = FakeQueue()
    for _ in range(4):
        ah.emit_run_failed("auth", write_queue=q)
    with caplog.at_level("WARNING"):
        at.flush()
    assert sizes == [4, 2, 2]
    assert "413" in caplog.text
    assert at.pending() == [] and ah._iv.dropped == 0


@pytest.mark.parametrize("detail", [
    "EPT expired", "unpublished kid: a re-issued EPT will verify",
])
def test_401_expired_or_unpublished_kid_refreshes_once_and_retries(monkeypatch, detail):
    monkeypatch.delenv("WINDY_ADMIN_INGEST_TOKEN")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _ept("ET26-TEST-0001"))
    monkeypatch.setattr(at, "_ingest_target", lambda: (
        "https://ingest.test", os.environ["ETERNITAS_PASSPORT_TOKEN"]))
    refreshed: list[bool] = []

    def fake_refresh(force=False, **_k):
        refreshed.append(force)
        os.environ["ETERNITAS_PASSPORT_TOKEN"] = _ept("ET26-TEST-0001") + "new"
        return {"status": "refreshed"}

    import windyfly.eternitas.ept_refresh as er
    monkeypatch.setattr(er, "refresh_ept", fake_refresh)
    tokens: list[str] = []

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        tokens.append(headers["Authorization"])
        if len(tokens) == 1:
            return httpx.Response(401, json={"detail": detail})
        return httpx.Response(202, json={"accepted": 1, "quarantined": 0})

    monkeypatch.setattr(httpx, "post", fake_post)
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    at.flush()
    assert refreshed == [True]
    assert len(tokens) == 2 and tokens[0] != tokens[1]
    assert not at.breaker_open()


def test_401_expired_that_still_fails_after_refresh_trips_the_breaker(monkeypatch):
    monkeypatch.delenv("WINDY_ADMIN_INGEST_TOKEN")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _ept("ET26-TEST-0001"))
    monkeypatch.setattr(at, "_ingest_target", lambda: (
        "https://ingest.test", os.environ["ETERNITAS_PASSPORT_TOKEN"]))
    import windyfly.eternitas.ept_refresh as er

    def fake_refresh(force=False, **_k):
        os.environ["ETERNITAS_PASSPORT_TOKEN"] = _ept("ET26-TEST-0001") + "new"
        return {"status": "refreshed"}

    monkeypatch.setattr(er, "refresh_ept", fake_refresh)
    calls: list = []

    def fake_post(*a, **k):
        calls.append(1)
        return httpx.Response(401, json={"detail": "EPT expired"})

    monkeypatch.setattr(httpx, "post", fake_post)
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    at.flush()
    assert len(calls) == 2  # the original + ONE retry, then the breaker
    assert at.breaker_open()


@pytest.mark.parametrize("status,detail", [
    (401, "passport revoked"), (401, "issuer mismatch"), (403, "Unknown service token"),
])
def test_revoked_issuer_or_403_trips_the_breaker_without_refresh(monkeypatch, status, detail):
    monkeypatch.delenv("WINDY_ADMIN_INGEST_TOKEN")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _ept("ET26-TEST-0001"))
    monkeypatch.setattr(at, "_ingest_target", lambda: (
        "https://ingest.test", os.environ["ETERNITAS_PASSPORT_TOKEN"]))
    import windyfly.eternitas.ept_refresh as er

    def no_refresh(*a, **k):
        raise AssertionError("must not refresh")

    monkeypatch.setattr(er, "refresh_ept", no_refresh)
    calls: list = []

    def fake_post(*a, **k):
        calls.append(1)
        return httpx.Response(status, json={"detail": detail})

    monkeypatch.setattr(httpx, "post", fake_post)
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    at.flush()
    assert calls == [1] and at.breaker_open()


# ── consent gate: nothing leaves a customer machine before disclosure ──

def _customer(monkeypatch):
    """A customer install: no fleet pair, an EPT, outside pytest's guard."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.delenv("WINDY_ADMIN_INGEST_TOKEN")
    monkeypatch.delenv("WINDY_ADMIN_INGEST_URL")
    monkeypatch.delenv("WINDY_AGENT_PASSPORT")
    monkeypatch.setattr(at, "_ensure_flusher", lambda: None)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _ept("ET26-ABCD-1234"))
    seen: list[dict] = []
    monkeypatch.setattr(httpx, "post", lambda url, json=None, headers=None, timeout=None:  # noqa: A006
                        seen.append(json) or httpx.Response(202, json={"accepted": 1}))
    return seen


def test_no_disclosure_marker_means_nothing_is_built_or_sent(monkeypatch):
    seen = _customer(monkeypatch)
    q = FakeQueue()
    assert not disclosure.disclosed()
    assert ah.emit_run_failed("auth", write_queue=q) is None
    assert ah.health_row(q) is None
    at.flush()
    assert seen == [] and at.pending() == [] and ah._iv.dropped == 0
    assert at.target_kind() == "none"


def test_after_a_hatch_the_marker_exists_and_rows_send(monkeypatch, tmp_path):
    seen = _customer(monkeypatch)
    shown: list[str] = []
    assert disclosure.after_hatch(shown.append)
    assert shown == [disclosure.LINE]
    marker = tmp_path / "telemetry_disclosed"
    assert marker.exists() and (marker.stat().st_mode & 0o777) == 0o600
    assert not disclosure.after_hatch(shown.append)  # once
    assert at.target_kind() == "passport"
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    at.flush()
    assert len(seen) == 1


def test_windy_telemetry_1_is_explicit_consent(monkeypatch):
    seen = _customer(monkeypatch)
    monkeypatch.setenv("WINDY_TELEMETRY", "1")
    assert not disclosure.disclosed()
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    at.flush()
    assert len(seen) == 1


def test_fleet_pair_is_exempt_from_the_disclosure_gate(posted):
    assert not disclosure.disclosed()
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    assert len(_rows(posted)) == 1
    assert at.target_kind() == "fleet"


def test_windy_telemetry_off_command_wins_even_after_disclosure(monkeypatch):
    seen = _customer(monkeypatch)
    disclosure.after_hatch(lambda _l: None)
    disclosure.set_preference("off")
    assert ah.emit_run_failed("auth", write_queue=FakeQueue()) is None
    at.flush()
    assert seen == []
    assert not disclosure.after_hatch(lambda _l: None)  # opted out: no line


def test_windy_telemetry_cli_status_on_off(monkeypatch, capsys):
    import argparse

    from windyfly import cli

    seen = _customer(monkeypatch)
    cli._cmd_telemetry(argparse.Namespace(action="status"))
    out = capsys.readouterr().out
    assert "never your" in out                     # status discloses
    assert "own passport token" in out and "Disclosed:   yes" in out
    assert "Paused:      no" in out
    cli._cmd_telemetry(argparse.Namespace(action="off"))
    assert disclosure.preference() == "off"
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    at.flush()
    assert seen == []
    cli._cmd_telemetry(argparse.Namespace(action="on"))
    assert disclosure.preference() == "on"
    ah.emit_run_failed("auth", write_queue=FakeQueue())
    at.flush()
    assert len(seen) == 1


def test_status_shows_the_breaker(monkeypatch, capsys, tmp_path):
    import argparse

    from windyfly import cli

    _customer(monkeypatch)
    (tmp_path / "telemetry_refused").write_text("x\n")
    cli._cmd_telemetry(argparse.Namespace(action="status"))
    assert "refused this install's credentials" in capsys.readouterr().out
