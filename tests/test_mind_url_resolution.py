"""One name for the Windy Mind host.

`MIND_API_URL` is canonical (it is what `windy go` writes and what the
model layer reads). `MIND_BASE_URL` is the legacy name the ADR-051
runtime-claim path used to read on its own. Both defaulted to
production, so a dev/staging override moved the brain while the claim
kept talking to PROD Mind — a silent prod-traffic trap. These tests pin
that every read site now resolves through `models.resolve_mind_url()`.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from windyfly import runtime_claim
from windyfly.agent import models


@pytest.fixture(autouse=True)
def _clean_mind_env(monkeypatch):
    monkeypatch.delenv("MIND_API_URL", raising=False)
    monkeypatch.delenv("MIND_BASE_URL", raising=False)
    models._reset_mind_url_log_for_tests()
    yield
    models._reset_mind_url_log_for_tests()


# ─── resolve_mind_url() ───────────────────────────────────────────────


def test_default_is_production_host():
    assert models.resolve_mind_url() == "https://api.windymind.ai"


def test_canonical_name_wins(monkeypatch):
    monkeypatch.setenv("MIND_API_URL", "http://mind.local:8900")
    monkeypatch.setenv("MIND_BASE_URL", "http://legacy.local:9999")
    assert models.resolve_mind_url() == "http://mind.local:8900"


def test_legacy_name_is_still_honored(monkeypatch):
    monkeypatch.setenv("MIND_BASE_URL", "http://legacy.local:9999")
    assert models.resolve_mind_url() == "http://legacy.local:9999"


def test_trailing_slash_stripped(monkeypatch):
    monkeypatch.setenv("MIND_API_URL", "http://mind.local:8900/")
    assert models.resolve_mind_url() == "http://mind.local:8900"


def test_legacy_fallback_logs_once(monkeypatch, caplog):
    monkeypatch.setenv("MIND_BASE_URL", "http://legacy.local:9999")

    with caplog.at_level(logging.INFO, logger="windyfly.agent.models"):
        models.resolve_mind_url()
        models.resolve_mind_url()

    legacy_lines = [
        r for r in caplog.records if "MIND_BASE_URL" in r.getMessage()
    ]
    assert len(legacy_lines) == 1


def test_canonical_name_logs_nothing(monkeypatch, caplog):
    monkeypatch.setenv("MIND_API_URL", "http://mind.local:8900")

    with caplog.at_level(logging.INFO, logger="windyfly.agent.models"):
        models.resolve_mind_url()

    assert not caplog.records


# ─── every read site agrees ───────────────────────────────────────────


def _claim_host(monkeypatch) -> str:
    """Drive a real acquire_runtime_slot() and report the host it hit."""
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-AAAA")
    monkeypatch.setenv("WINDY_JWT", "fake.jwt.value")
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return httpx.Response(200, json={"claimed": True, "ttl_seconds": 90})

    runtime_claim._reset_state_for_tests()
    try:
        out = runtime_claim.acquire_runtime_slot(
            transport=httpx.MockTransport(handler)
        )
        assert out == runtime_claim.ClaimOutcome.GRANTED
    finally:
        runtime_claim._reset_state_for_tests()
    return captured["url"]


def test_runtime_claim_follows_the_canonical_override(monkeypatch):
    """The trap this repair closes: with only the canonical var set, the
    claim used to keep POSTing to PRODUCTION Mind while the model layer
    followed the override."""
    monkeypatch.setenv("MIND_API_URL", "https://mind.staging.test")
    assert _claim_host(monkeypatch) == (
        "https://mind.staging.test/v1/runtime/claim"
    )


def test_runtime_claim_still_honors_the_legacy_name(monkeypatch):
    monkeypatch.setenv("MIND_BASE_URL", "https://mind.legacy.test")
    assert _claim_host(monkeypatch) == (
        "https://mind.legacy.test/v1/runtime/claim"
    )


def test_broker_status_url_follows_the_same_resolver(monkeypatch):
    monkeypatch.setenv("MIND_BASE_URL", "https://mind.legacy.test")
    assert models.mind_broker_status()["url"] == "https://mind.legacy.test"


def test_quickstart_writes_the_canonical_name(tmp_path, monkeypatch):
    """The generated .env must use the name every read site prefers."""
    from windyfly import quickstart as qs

    monkeypatch.setattr(qs, "PROJECT_ROOT", tmp_path)
    qs.write_keyless_config()
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"{models.MIND_URL_ENV}={models.MIND_DEFAULT_URL}" in env
    assert models.MIND_URL_ENV_LEGACY not in env
