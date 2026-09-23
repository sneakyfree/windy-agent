"""Windy Admin llm.call telemetry (ADR-WA-001).

Contract: envelope only when configured AND the fly knows its passport;
tokens/cost as integers (microcents); delivery rides the write queue at
LOW priority; nothing here can raise into the agent loop.
"""

import base64
import json

import pytest

from windyfly.observability import admin_telemetry
from windyfly.observability.admin_telemetry import (
    build_llm_call_event,
    emit_llm_call,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "WINDY_ADMIN_INGEST_URL",
        "WINDY_ADMIN_INGEST_TOKEN",
        "WINDY_AGENT_PASSPORT",
        "WINDY_PASSPORT_EPT",
    ):
        monkeypatch.delenv(var, raising=False)
    admin_telemetry._passport_cache = None
    yield
    admin_telemetry._passport_cache = None


def _configure(monkeypatch, passport="ET26-WIND-Y000"):
    monkeypatch.setenv("WINDY_ADMIN_INGEST_URL", "https://admin.windyword.ai")
    monkeypatch.setenv("WINDY_ADMIN_INGEST_TOKEN", "wat_test")
    monkeypatch.setenv("WINDY_AGENT_PASSPORT", passport)


def test_unconfigured_returns_none():
    assert build_llm_call_event(
        model="claude-opus-4-8",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.01,
        session_id="s1",
        had_tool_calls=False,
    ) is None


def test_envelope_shape(monkeypatch):
    _configure(monkeypatch)
    event = build_llm_call_event(
        model="claude-opus-4-8",
        input_tokens=1200,
        output_tokens=340,
        cost_usd=0.0234,
        session_id="sess-1",
        had_tool_calls=True,
    )
    assert event["platform"] == "windy-agent"
    assert event["service"] == "fly"
    assert event["event_type"] == "llm.call"
    assert event["actor_type"] == "agent"
    assert event["actor_id"] == "ET26-WIND-Y000"
    assert event["model"] == "claude-opus-4-8"
    assert event["provider"] == "anthropic"
    assert event["tokens_in"] == 1200
    assert event["tokens_out"] == 340
    assert event["cost_microcents"] == 23400  # $0.0234
    assert event["metadata"] == {"had_tool_calls": True}
    assert event["ts"]


def test_passport_from_ept_sub(monkeypatch):
    monkeypatch.setenv("WINDY_ADMIN_INGEST_URL", "https://admin.windyword.ai")
    monkeypatch.setenv("WINDY_ADMIN_INGEST_TOKEN", "wat_test")
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "ET26-EPTS-UB01"}).encode()
    ).rstrip(b"=").decode()
    monkeypatch.setenv("WINDY_PASSPORT_EPT", f"hdr.{payload}.sig")
    event = build_llm_call_event(
        model="claude-opus-4-8",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
        session_id=None,
        had_tool_calls=False,
    )
    assert event["actor_id"] == "ET26-EPTS-UB01"


def test_no_passport_no_event(monkeypatch):
    monkeypatch.setenv("WINDY_ADMIN_INGEST_URL", "https://admin.windyword.ai")
    monkeypatch.setenv("WINDY_ADMIN_INGEST_TOKEN", "wat_test")
    assert build_llm_call_event(
        model="claude-opus-4-8",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
        session_id=None,
        had_tool_calls=False,
    ) is None


def test_emit_buffers_for_the_next_batch(monkeypatch):
    _configure(monkeypatch)
    admin_telemetry._reset_for_tests()

    class ExplodingQueue:
        def enqueue(self, *a):
            raise AssertionError("telemetry never rides the write queue now")

    emit_llm_call(
        ExplodingQueue(),
        model="claude-opus-4-8",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.001,
        session_id="s1",
        had_tool_calls=False,
    )
    (row,) = admin_telemetry.pending()
    assert row["event_type"] == "llm.call"
    admin_telemetry._reset_for_tests()


def test_emit_noop_and_never_raises_when_unconfigured():
    class ExplodingQueue:
        def enqueue(self, *a):
            raise AssertionError("must not enqueue when unconfigured")

    emit_llm_call(
        ExplodingQueue(),
        model="claude-opus-4-8",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
        session_id=None,
        had_tool_calls=False,
    )


def test_unknown_cost_is_absent_not_zero(monkeypatch):
    _configure(monkeypatch)
    event = build_llm_call_event(
        model="mystery-model", input_tokens=10, output_tokens=5,
        cost_usd=None, session_id="s", had_tool_calls=False,
    )
    assert "cost_microcents" not in event


def test_billing_and_cache_tokens_in_metadata(monkeypatch):
    _configure(monkeypatch)
    event = build_llm_call_event(
        model="claude-opus-5", input_tokens=10, output_tokens=5,
        cost_usd=0.5, session_id="s", had_tool_calls=False,
        billing="max_subscription", cache_read_tokens=100,
    )
    assert event["metadata"]["billing"] == "max_subscription"
    assert event["metadata"]["cache_read_tokens"] == 100
    assert event["cost_microcents"] == 500_000  # micro-USD


def test_emit_llm_record_skips_failed_calls(monkeypatch):
    from windyfly.observability.admin_telemetry import emit_llm_record

    _configure(monkeypatch)
    admin_telemetry._reset_for_tests()
    emit_llm_record(None, {"status": "failed", "model": "claude-opus-5"})
    assert admin_telemetry.pending() == []
    emit_llm_record(None, {"status": "ok", "model": "claude-opus-5",
                           "provider": "windy-mind", "cost_usd": 0.01,
                           "billing": "metered"})
    (row,) = admin_telemetry.pending()
    assert row["provider"] == "windymind"
    admin_telemetry._reset_for_tests()
