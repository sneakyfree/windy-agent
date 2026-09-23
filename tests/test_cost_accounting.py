"""Cost accounting (09-23): Claude priced correctly, every LLM call
recorded once (success or failure, every path), never a guessed price.

Found on Windy 0: its ledger said $0.14 for 934K claude-opus-5 input
tokens (~$4.70 at list price). COST_MAP had no Opus entry and unknown
models fell back to gpt-4o-mini's price. The ledger also held one row
per TURN, written by the loop only, so tool rounds, helper calls and
every failed call were missing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from windyfly.agent import models
from windyfly.agent.models import (
    _anthropic_cache_tokens,
    call_llm,
    estimate_cost,
    llm_purpose,
    model_prices,
    sum_costs,
)
from windyfly.memory.cost_ledger import install_cost_sink, log_cost
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


# ── prices ──────────────────────────────────────────────────────────


def test_opus_5_is_priced_as_opus_not_gpt_4o_mini():
    # The Windy 0 day: 934,491 input + 4,700 output tokens.
    cost = estimate_cost("claude-opus-5", 934_491, 4_700)
    assert cost == pytest.approx(934_491 * 5 / 1e6 + 4_700 * 25 / 1e6)
    assert cost > 4.5  # was $0.14


@pytest.mark.parametrize("model,inp,out", [
    ("claude-opus-5", 5.0, 25.0),
    ("claude-opus-4-8", 5.0, 25.0),
    ("claude-opus-5-5", 4.0, 20.0),
    ("claude-fable-5-1", 10.0, 50.0),
    ("claude-fable-5", 10.0, 50.0),
    ("claude-sonnet-5", 2.0, 10.0),
    ("claude-haiku-4-5", 1.0, 5.0),
])
def test_rate_card(model, inp, out):
    assert estimate_cost(model, 1_000_000, 0) == pytest.approx(inp)
    assert estimate_cost(model, 0, 1_000_000) == pytest.approx(out)


def test_longest_prefix_wins():
    # claude-opus-5-5 must never get claude-opus-5's (higher) price,
    # and a dated id falls to its family.
    assert model_prices("claude-opus-5-5")["input"] == 4.0
    assert model_prices("claude-opus-5-20260101")["input"] == 5.0
    assert model_prices("claude-fable-5-1")["cache_read"] == 0.25
    assert model_prices("anthropic/claude-sonnet-5")["input"] == 2.0
    assert model_prices("gpt-4o-mini")["input"] == pytest.approx(0.15)


def test_cache_tokens_are_priced_separately():
    cost = estimate_cost(
        "claude-opus-5", 1000, 100,
        cache_write_5m_tokens=10_000, cache_write_1h_tokens=2_000,
        cache_read_tokens=100_000,
    )
    expected = (1000 * 5 + 100 * 25 + 10_000 * 6.25 + 2_000 * 10 + 100_000 * 0.50) / 1e6
    assert cost == pytest.approx(expected)


def test_unknown_model_has_no_cost_and_warns_once(caplog):
    models._warned_unknown_models.discard("mystery-model-9")
    with caplog.at_level(logging.WARNING, logger="windyfly.agent.models"):
        assert estimate_cost("mystery-model-9", 1000, 1000) is None
        assert estimate_cost("mystery-model-9", 1000, 1000) is None
    warnings = [r for r in caplog.records if "mystery-model-9" in r.getMessage()]
    assert len(warnings) == 1


def test_old_generic_claude_prefixes_no_longer_misprice():
    # "claude-sonnet"/"claude-haiku" used to price every new model at
    # an old model's rate; an unknown Sonnet is now unknown.
    assert estimate_cost("claude-sonnet-4-6", 1000, 1000) is None
    assert model_prices("claude-haiku-4-5")["input"] == 1.0


def test_unknown_price_for_a_used_token_class_voids_the_whole_cost():
    # Fable 5's cache-read price is unconfirmed: no partial number.
    assert estimate_cost("claude-fable-5", 1000, 100, cache_read_tokens=50) is None
    assert estimate_cost("claude-fable-5", 1000, 100) is not None
    assert estimate_cost("claude-fable-5-1", 1000, 100, cache_read_tokens=50) is not None


def test_sum_costs():
    assert sum_costs([0.1, 0.2]) == pytest.approx(0.3)
    assert sum_costs([0.1, None]) is None
    assert sum_costs([]) == 0.0


def test_anthropic_cache_tokens():
    split = SimpleNamespace(
        cache_read_input_tokens=300, cache_creation_input_tokens=50,
        cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=20, ephemeral_1h_input_tokens=30),
    )
    assert _anthropic_cache_tokens(split) == (20, 30, 300)
    unsplit = SimpleNamespace(cache_read_input_tokens=7, cache_creation_input_tokens=40)
    assert _anthropic_cache_tokens(unsplit) == (40, 0, 7)
    # A MagicMock usage (as many tests build) must read as zero, not a Mock.
    assert _anthropic_cache_tokens(MagicMock()) == (0, 0, 0)


# ── per-call records from call_llm ──────────────────────────────────


def _provider(model, config=None):
    return {"provider_key": "anthropic", "type": "anthropic",
            "api_key": "sk-ant-oat01-test", "base_url": "https://api.anthropic.com"}


def _ok(*a, **kw):
    return {"content": "hi", "model": "claude-opus-5", "input_tokens": 1000,
            "output_tokens": 100, "cache_write_5m_tokens": 0,
            "cache_write_1h_tokens": 0, "cache_read_tokens": 10_000,
            "tool_calls": None}


class _RateLimited(Exception):
    status_code = 429


@pytest.fixture
def records():
    got: list[dict] = []
    models.set_cost_sink(got.append, "test")
    with patch.object(models, "_try_mind_broker", return_value=None), \
         patch.object(models, "get_provider_for_model", side_effect=_provider), \
         patch.object(models, "_is_provider_in_cooldown", return_value=False):
        yield got


def test_success_is_recorded_once_with_cost_and_billing(records):
    with patch.object(models, "_call_anthropic", side_effect=_ok):
        result = call_llm([{"role": "user", "content": "x"}], model="claude-opus-5")
    assert len(records) == 1
    rec = records[0]
    assert rec["status"] == "ok"
    assert rec["provider"] == "anthropic"
    assert rec["purpose"] == "chat"
    assert rec["billing"] == "max_subscription"  # an oat token: Max plan
    assert rec["cache_read_tokens"] == 10_000
    expected = (1000 * 5 + 100 * 25 + 10_000 * 0.5) / 1e6
    assert rec["cost_usd"] == pytest.approx(expected)
    assert result["cost_usd"] == pytest.approx(expected)
    assert result["billing"] == "max_subscription"


def test_failed_call_is_recorded_with_an_error_code(records):
    with patch.object(models, "_call_anthropic", side_effect=_RateLimited("429 rate_limit_error")), \
         patch.object(models, "_record_provider_failure"):
        with pytest.raises(RuntimeError):
            call_llm([{"role": "user", "content": "x"}], model="claude-opus-5")
    assert [r["status"] for r in records] == ["failed"]
    assert records[0]["error_code"] == "rate_limited"
    assert records[0]["http_status"] == 429
    assert records[0]["cost_usd"] is None


def test_no_provider_at_all_is_still_a_failed_record():
    got: list[dict] = []
    models.set_cost_sink(got.append, "test")
    no_key = {"provider_key": "openai", "type": "openai", "api_key": "",
              "base_url": "https://api.openai.com/v1"}
    with patch.object(models, "_try_mind_broker", return_value=None), \
         patch.object(models, "get_provider_for_model", return_value=no_key):
        with pytest.raises(RuntimeError):
            call_llm([{"role": "user", "content": "x"}], model="gpt-4o-mini")
    assert len(got) == 1 and got[0]["error_code"] == "no_provider"


def test_metered_key_is_metered(records):
    metered = dict(_provider(None), api_key="sk-ant-api03-test")
    with patch.object(models, "get_provider_for_model", return_value=metered), \
         patch.object(models, "_max_oauth_active", return_value=False), \
         patch.object(models, "_call_anthropic", side_effect=_ok):
        call_llm([{"role": "user", "content": "x"}], model="claude-opus-5")
    assert records[0]["billing"] == "metered"


def test_purpose_block_labels_helper_calls(records):
    with patch.object(models, "_call_anthropic", side_effect=_ok), llm_purpose("intent"):
        call_llm([{"role": "user", "content": "x"}], model="claude-opus-5")
    assert records[0]["purpose"] == "intent"


# ── the ledger ──────────────────────────────────────────────────────


def _wait_rows(db, n, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = db.fetchall("SELECT * FROM cost_ledger ORDER BY created_at")
        if len(rows) >= n:
            return rows
        time.sleep(0.02)
    return db.fetchall("SELECT * FROM cost_ledger ORDER BY created_at")


def test_ledger_sink_writes_success_and_failure_rows(tmp_path):
    from windyfly.agent import tracing

    db = Database(str(tmp_path / "ledger.db"))
    wq = WriteQueue()
    wq.start()
    try:
        install_cost_sink(db, wq)
        tracing.set_request_id("a" * 32)
        with patch.object(models, "_try_mind_broker", return_value=None), \
             patch.object(models, "get_provider_for_model", side_effect=_provider), \
             patch.object(models, "_is_provider_in_cooldown", return_value=False), \
             patch.object(models, "_record_provider_failure"):
            with patch.object(models, "_call_anthropic", side_effect=_ok):
                call_llm([{"role": "user", "content": "x"}], model="claude-opus-5")
            with patch.object(models, "_call_anthropic", side_effect=_RateLimited("429")):
                with pytest.raises(RuntimeError):
                    call_llm([{"role": "user", "content": "x"}], model="claude-opus-5")
        rows = _wait_rows(db, 2)
        assert len(rows) == 2
        ok = next(r for r in rows if r["status"] == "ok")
        failed = next(r for r in rows if r["status"] == "failed")
        assert ok["cost_usd"] > 0 and ok["billing"] == "max_subscription"
        assert ok["cache_read_tokens"] == 10_000
        # Captured in the caller's thread, not the write-queue worker's
        # (every row used to have request_id NULL).
        assert ok["request_id"] == "a" * 32
        assert failed["cost_usd"] is None and failed["error_code"] == "rate_limited"
    finally:
        wq.stop()
        db.close()


def test_install_cost_sink_is_idempotent(tmp_path):
    db = Database(str(tmp_path / "l.db"))
    wq = WriteQueue()
    install_cost_sink(db, wq)
    first = models._cost_sink
    install_cost_sink(db, wq)
    assert models._cost_sink is first
    db.close()


def test_unknown_cost_is_null_and_readers_cope(tmp_path):
    from windyfly.memory.cost_ledger import get_daily_spend

    db = Database(str(tmp_path / "n.db"))
    log_cost(db, "mystery", 10, 10, None)
    log_cost(db, "claude-opus-5", 10, 10, 0.25)
    row = db.fetchone("SELECT cost_usd FROM cost_ledger WHERE model = 'mystery'")
    assert row["cost_usd"] is None
    assert get_daily_spend(db) == pytest.approx(0.25)
    db.close()


def test_voice_bridge_turn_logs_every_call(tmp_path):
    """The voice bridge (`windyfly.bridge.uds_server`) runs the turn
    loop; each LLM call in its turn must land in the ledger."""
    from windyfly.bridge.uds_server import UDSBridge

    db = Database(str(tmp_path / "bridge.db"))
    wq = WriteQueue()
    wq.start()
    config = {
        "agent": {"default_model": "claude-opus-5"},
        "memory": {"max_episodes_per_context": 20, "max_nodes_per_context": 10},
        "personality": {},
        "costs": {"daily_budget_usd": 50.0},
    }
    try:
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch.object(models, "_try_mind_broker", return_value=None), \
             patch.object(models, "get_provider_for_model", side_effect=_provider), \
             patch.object(models, "_is_provider_in_cooldown", return_value=False), \
             patch.object(models, "_call_anthropic", side_effect=_ok) as fake:
            bridge = UDSBridge(config, db, wq)
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(bridge._dispatch("agent.respond", {
                    "message": "what's the capital of France?",
                    "session_id": "voice-1",
                }))
            finally:
                loop.close()
            calls = fake.call_count
        assert "response" in result
        rows = _wait_rows(db, calls)
        assert calls >= 1
        assert len(rows) == calls  # exactly one row per LLM call
        assert all(r["status"] == "ok" and r["cost_usd"] > 0 for r in rows)
    finally:
        wq.stop()
        db.close()
