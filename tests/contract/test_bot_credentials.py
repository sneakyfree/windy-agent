"""Contract tests for the wk_ bot-key minting flow.

Covers:
- POST /api/v1/identity/bot-keys/mint shape (path, headers, body)
- Cache round-trip
- 30-day rotation window
- Graceful fallback when no cache/JWT
- ecosystem_auth_header() prefers bot key, falls back to caller token
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from windyfly.auth import bot_credentials
from windyfly.auth.bot_credentials import (
    BotCredential,
    clear_cached_bot_key,
    ecosystem_auth_header,
    get_bot_key,
    mint_bot_key,
)

PRO_BASE = "https://pro.windy.test"


@pytest.fixture(autouse=True)
def _redirect_cache(tmp_path, monkeypatch):
    """Redirect the cache file into a tmp dir so tests don't touch real state."""
    fake_cache = tmp_path / "bot_key.json"
    monkeypatch.setattr(bot_credentials, "_CACHE_FILE", fake_cache)
    monkeypatch.setenv("WINDY_PRO_URL", PRO_BASE)
    clear_cached_bot_key()
    yield
    clear_cached_bot_key()


class TestMintContract:
    @respx.mock
    async def test_posts_correct_path_body_and_auth(self):
        route = respx.post(f"{PRO_BASE}/api/v1/identity/bot-keys/mint").mock(
            return_value=httpx.Response(200, json={
                "bot_key": "wk_live_abc123",
                "expires_at": "2027-04-16T00:00:00Z",
                "windy_identity_id": "wi_user_1",
            })
        )

        cred = await mint_bot_key(
            owner_jwt="owner_jwt_xyz",
            passport_number="ET-00042",
        )

        assert route.called
        req = route.calls.last.request
        assert req.headers["Authorization"] == "Bearer owner_jwt_xyz"
        body = json.loads(req.content)
        assert body["passport_number"] == "ET-00042"
        assert isinstance(body.get("scopes"), list)
        assert cred.bot_key == "wk_live_abc123"
        assert cred.windy_identity_id == "wi_user_1"
        assert cred.passport_number == "ET-00042"

    @respx.mock
    async def test_mint_caches_credential(self):
        respx.post(f"{PRO_BASE}/api/v1/identity/bot-keys/mint").mock(
            return_value=httpx.Response(200, json={
                "bot_key": "wk_cached",
                "expires_at": "2027-04-16T00:00:00Z",
            })
        )

        await mint_bot_key(owner_jwt="j", passport_number="ET-1")

        cached = bot_credentials._load_cached()
        assert cached is not None
        assert cached.bot_key == "wk_cached"

    async def test_mint_requires_url_jwt_and_passport(self, monkeypatch):
        monkeypatch.delenv("WINDY_PRO_URL", raising=False)
        monkeypatch.delenv("WINDY_API_URL", raising=False)
        with pytest.raises(RuntimeError):
            await mint_bot_key(owner_jwt="j", passport_number="ET-1")

        monkeypatch.setenv("WINDY_PRO_URL", PRO_BASE)
        with pytest.raises(RuntimeError):
            await mint_bot_key(owner_jwt="", passport_number="ET-1")
        with pytest.raises(RuntimeError):
            await mint_bot_key(owner_jwt="j", passport_number="")


class TestRotation:
    def _write_cached(self, expires_at: datetime, bot_key: str = "wk_existing"):
        bot_credentials._save_cached(BotCredential(
            bot_key=bot_key,
            expires_at=expires_at,
            windy_identity_id="wi_1",
            passport_number="ET-1",
        ))

    async def test_cached_key_returned_when_fresh(self):
        self._write_cached(datetime.now(timezone.utc) + timedelta(days=90))
        cred = await get_bot_key()
        assert cred is not None
        assert cred.bot_key == "wk_existing"

    @respx.mock
    async def test_rotates_within_30_day_window(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", "owner_jwt")
        self._write_cached(datetime.now(timezone.utc) + timedelta(days=15))
        route = respx.post(f"{PRO_BASE}/api/v1/identity/bot-keys/mint").mock(
            return_value=httpx.Response(200, json={
                "bot_key": "wk_rotated",
                "expires_at": "2027-04-16T00:00:00Z",
            })
        )

        cred = await get_bot_key()

        assert route.called
        assert cred is not None
        assert cred.bot_key == "wk_rotated"

    @respx.mock
    async def test_returns_stale_key_when_rotation_prereqs_missing(self, monkeypatch):
        monkeypatch.delenv("WINDY_JWT", raising=False)
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
        self._write_cached(datetime.now(timezone.utc) + timedelta(days=5))

        cred = await get_bot_key()

        assert cred is not None
        assert cred.bot_key == "wk_existing"

    async def test_no_cache_and_no_prereqs_returns_none(self, monkeypatch):
        monkeypatch.delenv("WINDY_JWT", raising=False)
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
        cred = await get_bot_key()
        assert cred is None

    @respx.mock
    async def test_mint_failure_during_rotation_keeps_stale(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", "owner_jwt")
        self._write_cached(datetime.now(timezone.utc) + timedelta(days=15))
        respx.post(f"{PRO_BASE}/api/v1/identity/bot-keys/mint").mock(
            return_value=httpx.Response(500, text="boom")
        )

        cred = await get_bot_key()

        assert cred is not None
        assert cred.bot_key == "wk_existing"


class TestEcosystemAuthHeader:
    async def test_prefers_cached_bot_key(self):
        bot_credentials._save_cached(BotCredential(
            bot_key="wk_primary",
            expires_at=datetime.now(timezone.utc) + timedelta(days=180),
        ))

        headers = await ecosystem_auth_header(fallback_token="owner_jwt_fallback")

        assert headers == {"Authorization": "Bearer wk_primary"}

    async def test_falls_back_to_caller_token(self):
        headers = await ecosystem_auth_header(fallback_token="service_token_x")
        assert headers == {"Authorization": "Bearer service_token_x"}

    async def test_returns_empty_dict_with_no_auth(self):
        headers = await ecosystem_auth_header()
        assert headers == {}


class TestMintLogHygiene:
    """Log level for mint outcomes (2026-07-06 backup investigation).

    An EPT-only agent (holds a passport token, not an owner JWT, and/or
    no WINDY_PRO_URL) CANNOT mint a wk_ bot key — that's the normal
    steady state, and it falls back to the EPT which every platform
    accepts. It must NOT cry WARNING on every ecosystem call (it did:
    'Bot key mint failed: WINDY_PRO_URL not configured' 2-4x per Windy 0
    backup). Deterministic can't-mint states log at DEBUG; a real mint
    attempt that fails over the wire still logs WARNING."""

    import logging

    async def test_not_configured_is_debug_not_warning(self, monkeypatch, caplog):
        monkeypatch.delenv("WINDY_PRO_URL", raising=False)
        monkeypatch.delenv("WINDY_API_URL", raising=False)
        monkeypatch.setenv("WINDY_JWT", "ept-passport-token")
        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-T11V-NPD1")
        clear_cached_bot_key()
        with caplog.at_level(self.logging.DEBUG, logger="windyfly.auth.bot_credentials"):
            cred = await get_bot_key()
        assert cred is None  # nothing to mint, no cache
        warnings = [r for r in caplog.records if r.levelno >= self.logging.WARNING]
        assert not warnings, f"expected no WARNING, got {[r.message for r in warnings]}"
        assert any("passport-token fallback" in r.message for r in caplog.records)

    @respx.mock
    async def test_real_mint_http_failure_is_warning(self, monkeypatch, caplog):
        # URL + owner JWT present, no cache → a genuine mint attempt that
        # 500s IS worth a WARNING.
        monkeypatch.setenv("WINDY_JWT", "owner_jwt")
        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-T11V-NPD1")
        clear_cached_bot_key()
        respx.post(f"{PRO_BASE}/api/v1/identity/bot-keys/mint").mock(
            return_value=httpx.Response(500, text="boom")
        )
        with caplog.at_level(self.logging.DEBUG, logger="windyfly.auth.bot_credentials"):
            cred = await get_bot_key()
        assert cred is None
        assert any(
            r.levelno >= self.logging.WARNING and "unexpectedly" in r.message
            for r in caplog.records
        )
