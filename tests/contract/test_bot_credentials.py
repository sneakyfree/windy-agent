"""Contract tests for the wk_ bot-key minting flow.

The route under test is windy-pro's REAL one,
`POST /api/v1/identity/api-keys` (account-server
`src/routes/identity.ts`). This file previously pinned
`/api/v1/identity/bot-keys/mint`, which has never existed on any
account-server — the tests passed against a mock of a 404.

Covers:
- POST /api/v1/identity/api-keys shape (path, headers, body)
- 201 response parsing (apiKey / id / expiresAt / scopes)
- Explicit skip when no bot identity id is available
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
    BotIdentityUnavailable,
    clear_cached_bot_key,
    ecosystem_auth_header,
    get_bot_key,
    mint_bot_key,
)

PRO_BASE = "https://pro.windy.test"
MINT_URL = f"{PRO_BASE}/api/v1/identity/api-keys"
BOT_ID = "bot_identity_9f2c"

# A verbatim 201 body from account-server's POST /api/v1/identity/api-keys.
MINT_201 = {
    "apiKey": "wk_live_abc123",
    "keyPrefix": "wk_live_abc",
    "id": "b2f0c0de-0000-4000-8000-000000000001",
    "scopes": ["mail:send", "cloud:upload"],
    "expiresAt": "2027-04-16T00:00:00Z",
    "warning": "Store this API key securely. It will not be shown again.",
}


@pytest.fixture(autouse=True)
def _redirect_cache(tmp_path, monkeypatch):
    """Redirect the cache file into a tmp dir so tests don't touch real state."""
    fake_cache = tmp_path / "bot_key.json"
    monkeypatch.setattr(bot_credentials, "_CACHE_FILE", fake_cache)
    monkeypatch.setenv("WINDY_PRO_URL", PRO_BASE)
    monkeypatch.delenv("BOT_IDENTITY_ID", raising=False)
    monkeypatch.delenv("WINDY_BOT_IDENTITY_ID", raising=False)
    clear_cached_bot_key()
    yield
    clear_cached_bot_key()


class TestMintContract:
    @respx.mock
    async def test_posts_correct_path_body_and_auth(self):
        route = respx.post(MINT_URL).mock(return_value=httpx.Response(201, json=MINT_201))

        cred = await mint_bot_key(
            owner_jwt="owner_jwt_xyz",
            passport_number="ET-00042",
            scopes=["mail:send", "cloud:upload"],
            bot_identity_id=BOT_ID,
        )

        assert route.called
        req = route.calls.last.request
        assert req.headers["Authorization"] == "Bearer owner_jwt_xyz"
        body = json.loads(req.content)
        # The server keys the whole call on identityId; passport_number
        # is NOT a field it understands.
        assert body["identityId"] == BOT_ID
        assert body["scopes"] == ["mail:send", "cloud:upload"]
        assert "passport_number" not in body
        # expiresInDays is mandatory in practice — without it the server
        # returns no expiresAt and rotation has nothing to work from.
        assert body["expiresInDays"] > 0
        assert "ET-00042" in body["label"]
        assert cred.passport_number == "ET-00042"

    @respx.mock
    async def test_parses_201_response_fields(self):
        respx.post(MINT_URL).mock(return_value=httpx.Response(201, json=MINT_201))

        cred = await mint_bot_key(
            owner_jwt="j", passport_number="ET-1", bot_identity_id=BOT_ID,
        )

        assert cred.bot_key == "wk_live_abc123"
        assert cred.key_id == "b2f0c0de-0000-4000-8000-000000000001"
        assert cred.scopes == ["mail:send", "cloud:upload"]
        assert cred.expires_at == datetime(2027, 4, 16, tzinfo=timezone.utc)
        # windy_identity_id now holds the BOT's identity, not the owner's.
        assert cred.windy_identity_id == BOT_ID

    @respx.mock
    async def test_missing_expires_at_falls_back_to_requested_window(self):
        """Older servers omit expiresAt when expiresInDays wasn't stored.
        Rotation still needs a datetime — assume what we asked for."""
        respx.post(MINT_URL).mock(return_value=httpx.Response(201, json={
            "apiKey": "wk_no_expiry", "keyPrefix": "wk_no_expir", "id": "k1",
        }))

        cred = await mint_bot_key(
            owner_jwt="j", passport_number="ET-1",
            bot_identity_id=BOT_ID, expires_in_days=90,
        )

        assert cred.expires_at > datetime.now(timezone.utc) + timedelta(days=89)

    @respx.mock
    async def test_mint_caches_credential(self):
        respx.post(MINT_URL).mock(return_value=httpx.Response(201, json={
            **MINT_201, "apiKey": "wk_cached",
        }))

        await mint_bot_key(owner_jwt="j", passport_number="ET-1", bot_identity_id=BOT_ID)

        cached = bot_credentials._load_cached()
        assert cached is not None
        assert cached.bot_key == "wk_cached"
        assert cached.windy_identity_id == BOT_ID

    async def test_mint_requires_url_jwt_and_passport(self, monkeypatch):
        monkeypatch.delenv("WINDY_PRO_URL", raising=False)
        monkeypatch.delenv("WINDY_API_URL", raising=False)
        with pytest.raises(RuntimeError):
            await mint_bot_key(owner_jwt="j", passport_number="ET-1", bot_identity_id=BOT_ID)

        monkeypatch.setenv("WINDY_PRO_URL", PRO_BASE)
        with pytest.raises(RuntimeError):
            await mint_bot_key(owner_jwt="", passport_number="ET-1", bot_identity_id=BOT_ID)
        with pytest.raises(RuntimeError):
            await mint_bot_key(owner_jwt="j", passport_number="", bot_identity_id=BOT_ID)

    @respx.mock
    async def test_4xx_surfaces_as_http_error(self):
        """The server 400s when identityId isn't a bot. That must reach
        the caller, not be swallowed into a half-success."""
        respx.post(MINT_URL).mock(return_value=httpx.Response(400, json={
            "error": "API keys can only be created for bot identities",
        }))

        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await mint_bot_key(
                owner_jwt="j", passport_number="ET-1", bot_identity_id="owner_identity_1",
            )

        assert excinfo.value.response.status_code == 400
        assert bot_credentials._load_cached() is None, "a 4xx must not cache anything"

    @respx.mock
    async def test_403_when_caller_is_not_the_operator(self):
        respx.post(MINT_URL).mock(return_value=httpx.Response(403, json={
            "error": "Only the bot operator or an admin can create API keys",
        }))

        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await mint_bot_key(
                owner_jwt="someone_elses_jwt", passport_number="ET-1", bot_identity_id=BOT_ID,
            )

        assert excinfo.value.response.status_code == 403


class TestBotIdentityResolution:
    """Where the bot identity id comes from — and what happens when it
    doesn't come from anywhere (the terminal-lane reality)."""

    @respx.mock
    async def test_falls_back_to_env_var(self, monkeypatch):
        monkeypatch.setenv("BOT_IDENTITY_ID", "bot_from_env")
        route = respx.post(MINT_URL).mock(return_value=httpx.Response(201, json=MINT_201))

        await mint_bot_key(owner_jwt="j", passport_number="ET-1")

        assert json.loads(route.calls.last.request.content)["identityId"] == "bot_from_env"

    @respx.mock
    async def test_falls_back_to_cached_identity_for_rotation(self, monkeypatch):
        """A re-mint doesn't need to be told the identity again — the
        previous mint cached it."""
        bot_credentials._save_cached(BotCredential(
            bot_key="wk_old",
            expires_at=datetime.now(timezone.utc) + timedelta(days=5),
            windy_identity_id="bot_from_cache",
            passport_number="ET-1",
        ))
        route = respx.post(MINT_URL).mock(return_value=httpx.Response(201, json=MINT_201))

        await mint_bot_key(owner_jwt="j", passport_number="ET-1")

        assert json.loads(route.calls.last.request.content)["identityId"] == "bot_from_cache"

    @respx.mock
    async def test_explicit_argument_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("BOT_IDENTITY_ID", "bot_from_env")
        route = respx.post(MINT_URL).mock(return_value=httpx.Response(201, json=MINT_201))

        await mint_bot_key(owner_jwt="j", passport_number="ET-1", bot_identity_id="bot_explicit")

        assert json.loads(route.calls.last.request.content)["identityId"] == "bot_explicit"

    @respx.mock
    async def test_skips_explicitly_when_no_bot_identity(self, monkeypatch):
        """Terminal-lane hatch: an Eternitas passport but no windy-pro
        bot row. Refuse loudly; never guess, never pretend."""
        monkeypatch.setenv("WINDY_IDENTITY_ID", "owner_identity_1")  # the OWNER's — must not be used
        route = respx.post(MINT_URL).mock(return_value=httpx.Response(201, json=MINT_201))

        with pytest.raises(BotIdentityUnavailable) as excinfo:
            await mint_bot_key(owner_jwt="j", passport_number="ET-1")

        assert not route.called, "no request may be sent without a bot identity"
        assert "skipped: no bot identity id" in str(excinfo.value)
        assert "BOT_IDENTITY_ID" in str(excinfo.value)

    async def test_skip_is_not_reported_as_a_mint_failure(self, monkeypatch, caplog):
        """get_bot_key() logs the skip at INFO with the honest reason —
        not a WARNING (it isn't a failure) and not nothing at all."""
        import logging

        monkeypatch.setenv("WINDY_JWT", "owner_jwt")
        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-T11V-NPD1")
        clear_cached_bot_key()

        with caplog.at_level(logging.DEBUG, logger="windyfly.auth.bot_credentials"):
            cred = await get_bot_key()

        assert cred is None
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("no bot identity id" in r.getMessage() for r in caplog.records)


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
        route = respx.post(MINT_URL).mock(
            return_value=httpx.Response(201, json={**MINT_201, "apiKey": "wk_rotated"})
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
        respx.post(MINT_URL).mock(return_value=httpx.Response(500, text="boom"))

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
        # URL + owner JWT + bot identity present, no cache → a genuine
        # mint attempt that 500s IS worth a WARNING.
        monkeypatch.setenv("WINDY_JWT", "owner_jwt")
        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-T11V-NPD1")
        monkeypatch.setenv("BOT_IDENTITY_ID", BOT_ID)
        clear_cached_bot_key()
        respx.post(MINT_URL).mock(return_value=httpx.Response(500, text="boom"))
        with caplog.at_level(self.logging.DEBUG, logger="windyfly.auth.bot_credentials"):
            cred = await get_bot_key()
        assert cred is None
        assert any(
            r.levelno >= self.logging.WARNING and "unexpectedly" in r.message
            for r in caplog.records
        )
