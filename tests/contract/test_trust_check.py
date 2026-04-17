"""Contract tests for the trust check + gate.

Validates against the shape in /Users/thewindstorm/eternitas/docs/trust-api.md:
- GET /api/v1/trust/{passport} (note /api/v1/, not /v1/)
- Response fields: status, band, clearance_level, tier_multiplier,
  integrity_score, dimensions, allowed_actions, denied_actions,
  evaluated_at, cache_ttl_seconds
- 5-minute SQLite cache hit/miss and expiry (TTL honoured from body)
- Action mapping: agent gate vocabulary → Eternitas vocabulary
- Humans/no-passport → gate skipped
- trust.changed webhook invalidates cache (band pair and clearance pair)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from windyfly.memory.database import Database
from windyfly.trust import TrustDenied, TrustSnapshot, check_trust, invalidate_trust_cache, require_trust
from windyfly.trust.check import (
    CACHE_TTL,
    _cache_read,
    _cache_write,
    get_trust,
    map_gate_action,
)
from windyfly.trust.gate import require_trust_sync
from windyfly.trust.webhook import handle_trust_changed

ETERNITAS_BASE = "https://eternitas.windy.test"
PASSPORT = "ET26-K7BF-42MN"


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "agent.db")
    return Database(path)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ETERNITAS_URL", ETERNITAS_BASE)
    monkeypatch.delenv("ETERNITAS_API_URL", raising=False)
    monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)
    monkeypatch.delenv("ETERNITAS_USE_MOCK", raising=False)
    monkeypatch.delenv("WINDYFLY_TRUST_STRICT", raising=False)
    monkeypatch.setenv("WINDYFLY_DB_PATH", str(tmp_path / "agent.db"))


def _live_response_body(**overrides) -> dict:
    """A canonical live Eternitas response body — override per-test."""
    body = {
        "passport_number": PASSPORT,
        "status": "active",
        "integrity_score": 842,
        "dimensions": {
            "honesty": 900, "reliability": 850, "compliance": 800,
            "safety": 780, "reputation": 820,
        },
        "band": "good",
        "clearance_level": "cleared",
        "tier_multiplier": 1.5,
        "allowed_actions": ["read", "send", "execute", "dm_bots", "install_packages"],
        "denied_actions": ["commit_push", "broadcast", "mention_strangers", "bypass_rate_caps"],
        "cache_ttl_seconds": 300,
        "evaluated_at": "2026-04-16T20:11:03.118+00:00",
    }
    body.update(overrides)
    return body


class TestActionMapping:
    def test_gate_vocabulary_maps_to_eternitas(self):
        assert map_gate_action("send_email") == "send"
        assert map_gate_action("post_chat_message") == "send"
        assert map_gate_action("run_command") == "execute"
        assert map_gate_action("install_package") == "install_packages"
        assert map_gate_action("commit_push") == "commit_push"
        assert map_gate_action("upload_file") == "send"

    def test_unknown_action_passes_through(self):
        assert map_gate_action("some_future_action") == "some_future_action"


class TestTrustFetchContract:
    @respx.mock
    async def test_correct_path_and_parse(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body())
        )

        snap = await get_trust(PASSPORT, db=db)

        assert snap is not None
        assert snap.status == "active"
        assert snap.band == "good"
        assert snap.clearance_level == "cleared"
        assert snap.tier_multiplier == 1.5
        assert snap.integrity_score == 842
        assert "send" in snap.allowed_actions
        assert "commit_push" in snap.denied_actions
        assert snap.dimensions["honesty"] == 900

    @respx.mock
    async def test_no_auth_header_sent(self, db):
        route = respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body())
        )

        await get_trust(PASSPORT, db=db)

        assert "Authorization" not in route.calls.last.request.headers

    @respx.mock
    async def test_caches_snapshot(self, db):
        route = respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body())
        )

        await get_trust(PASSPORT, db=db)
        await get_trust(PASSPORT, db=db)
        await get_trust(PASSPORT, db=db)

        assert route.call_count == 1

    @respx.mock
    async def test_cache_ttl_from_body_honoured(self, db):
        route = respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body(cache_ttl_seconds=60))
        )

        await get_trust(PASSPORT, db=db)
        # Fast-forward the cached_at by 90 seconds, past the 60s TTL.
        db.conn.execute(
            "UPDATE trust_cache SET cached_at = ? WHERE passport = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(), PASSPORT),
        )
        db.conn.commit()

        await get_trust(PASSPORT, db=db)
        assert route.call_count == 2

    @respx.mock
    async def test_404_returns_none(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        assert await get_trust(PASSPORT, db=db) is None

    @respx.mock
    async def test_429_returns_none_without_crashing(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "60"})
        )
        assert await get_trust(PASSPORT, db=db) is None

    @respx.mock
    async def test_400_unrecognised_prefix_returns_none(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/XYZ-garbage").mock(
            return_value=httpx.Response(400, json={"error": "bad prefix"})
        )
        assert await get_trust("XYZ-garbage", db=db) is None

    @respx.mock
    async def test_mock_flag_skips_live_fetch(self, monkeypatch, db):
        monkeypatch.setenv("ETERNITAS_USE_MOCK", "true")
        route = respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body())
        )

        assert await get_trust(PASSPORT, db=db) is None
        assert not route.called


class TestGate:
    @respx.mock
    async def test_exceptional_bot_allowed_everything_in_vocabulary(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body(
                band="exceptional",
                clearance_level="eternal",
                tier_multiplier=5.0,
                allowed_actions=[
                    "read", "send", "execute", "dm_bots", "install_packages",
                    "commit_push", "broadcast", "mention_strangers", "bypass_rate_caps",
                ],
                denied_actions=[],
            ))
        )
        for gate_action in ("send_email", "post_chat_message", "run_command",
                            "install_package", "commit_push", "upload_file"):
            decision = await require_trust(gate_action, db=db)
            assert decision.allowed, f"{gate_action} should be allowed"

    @respx.mock
    async def test_critical_band_blocks_everything(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body(
                band="critical", tier_multiplier=0.0, allowed_actions=[],
            ))
        )
        for gate_action in ("send_email", "run_command", "commit_push", "upload_file"):
            with pytest.raises(TrustDenied):
                await require_trust(gate_action, db=db)

    @respx.mock
    async def test_suspended_status_blocks_everything(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body(
                status="suspended",
                allowed_actions=["read", "send"],  # should be ignored
            ))
        )
        with pytest.raises(TrustDenied):
            await require_trust("send_email", db=db)

    @respx.mock
    async def test_revoked_status_blocks_everything(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body(
                status="revoked", allowed_actions=[],
            ))
        )
        with pytest.raises(TrustDenied):
            await require_trust("send_email", db=db)

    async def test_human_without_passport_skips_gate(self, db, monkeypatch):
        """No passport → gate is skipped entirely. No HTTP attempted."""
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
        with respx.mock(assert_all_called=False) as mock:
            catch_all = mock.get(url__regex=rf"{ETERNITAS_BASE}/api/v1/trust/.*")

            decision = await require_trust("send_email", db=db, passport="")

        assert decision.allowed
        assert "no passport" in decision.reason
        assert not catch_all.called

    async def test_sync_gate_uses_cache(self, db):
        _cache_write(
            TrustSnapshot(
                passport=PASSPORT, status="active", band="good",
                clearance_level="cleared", tier_multiplier=1.5,
                integrity_score=800,
                allowed_actions=["read", "send"],
            ),
            db=db,
        )

        decision = require_trust_sync("send_email", db=db)
        assert decision.allowed

        with pytest.raises(TrustDenied):
            require_trust_sync("run_command", db=db)

    async def test_sync_gate_in_event_loop_fails_open_by_default(self, db, monkeypatch):
        monkeypatch.delenv("ETERNITAS_URL", raising=False)
        monkeypatch.delenv("ETERNITAS_API_URL", raising=False)
        monkeypatch.delenv("WINDYFLY_TRUST_STRICT", raising=False)
        decision = require_trust_sync("run_command", db=db)
        assert decision.allowed
        assert "fail-open" in decision.reason

    async def test_strict_mode_fails_closed_without_snapshot(self, db, monkeypatch):
        monkeypatch.delenv("ETERNITAS_URL", raising=False)
        monkeypatch.delenv("ETERNITAS_API_URL", raising=False)
        monkeypatch.setenv("WINDYFLY_TRUST_STRICT", "1")
        decision = await check_trust("send_email", db=db)
        assert not decision.allowed
        assert "strict" in decision.reason


class TestWebhookInvalidation:
    async def test_band_change_invalidates_cache(self, db):
        _cache_write(
            TrustSnapshot(
                passport=PASSPORT, status="active", band="good",
                clearance_level="cleared", tier_multiplier=1.5,
                integrity_score=800, allowed_actions=["read", "send"],
            ),
            db=db,
        )
        assert _cache_read(PASSPORT, db=db) is not None

        await handle_trust_changed(
            {
                "event": "trust.changed",
                "event_type": "trust.changed",
                "passport_number": PASSPORT,
                "passport": PASSPORT,
                "old_band": "good",
                "new_band": "fair",
                "old_clearance": None,
                "new_clearance": None,
                "reason": "integrity_band: good->fair",
            },
            db=db,
        )

        assert _cache_read(PASSPORT, db=db) is None

    async def test_suspended_webhook_invalidates_cache(self, db):
        _cache_write(
            TrustSnapshot(
                passport=PASSPORT, status="active", band="good",
                clearance_level="cleared", allowed_actions=["read", "send"],
            ),
            db=db,
        )
        await handle_trust_changed(
            {
                "event_type": "trust.changed",
                "passport_number": PASSPORT,
                "old_band": "good", "new_band": "critical",
                "reason": "suspended for abuse",
            },
            db=db,
        )
        assert _cache_read(PASSPORT, db=db) is None

    async def test_revoked_webhook_invalidates_cache(self, db):
        _cache_write(
            TrustSnapshot(
                passport=PASSPORT, status="active", band="good", clearance_level="cleared",
            ),
            db=db,
        )
        await handle_trust_changed(
            {
                "event_type": "trust.changed",
                "passport_number": PASSPORT,
                "old_band": "good", "new_band": "critical",
                "reason": "passport revoked",
            },
            db=db,
        )
        assert _cache_read(PASSPORT, db=db) is None

    async def test_direction_band_change(self, db):
        result = await handle_trust_changed(
            {"event_type": "trust.changed", "passport_number": PASSPORT,
             "old_band": "good", "new_band": "fair"},
            db=db,
        )
        assert result.direction == "dropped"

        invalidate_trust_cache(PASSPORT, db=db)

        result = await handle_trust_changed(
            {"event_type": "trust.changed", "passport_number": PASSPORT,
             "old_band": "fair", "new_band": "exceptional"},
            db=db,
        )
        assert result.direction == "improved"

    async def test_direction_clearance_promotion(self, db):
        result = await handle_trust_changed(
            {
                "event_type": "trust.changed",
                "passport_number": PASSPORT,
                "old_band": None, "new_band": None,
                "old_clearance": "verified", "new_clearance": "cleared",
                "reason": "clearance promoted",
            },
            db=db,
        )
        assert result.direction == "improved"
        assert result.old_clearance == "verified"
        assert result.new_clearance == "cleared"
