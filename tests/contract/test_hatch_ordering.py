"""Contract tests for P1-E3 (shared-JWKS coupling) and P1-E4
(wk_ bot key minted before cloud quota).

P1-E3 — link_passport_with_identity sends the same owner JWT as
Bearer to both Windy Pro and Windy Cloud. This works only because
both services validate against a shared JWKS. The test proves that
a half-linked state (one 200, one 401) surfaces as a per-service
status in the summary dict, not a global failure.

P1-E4 — before this fix, _step_cloud_quota fell back to the owner
JWT because no wk_ key was minted yet at hatch time. Now
_step_mint_bot_key runs between link-passport and the
matrix/mail/phone fan-out, and cloud_quota uses the wk_ key when
available.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from windyfly.eternitas.provision import link_passport_with_identity
from windyfly.hatch_orchestrator import (
    HatchResult,
    _step_cloud_quota,
    _step_mint_bot_key,
    orchestrate_hatch,
)
from windyfly.memory.database import Database


PRO = "https://pro.windy.test"
CLOUD = "https://cloud.windy.test"


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "agent.db"))
    yield d
    d.close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for k in (
        "WINDY_PRO_URL", "WINDY_API_URL", "WINDY_CLOUD_URL", "WINDY_JWT",
        "WINDY_IDENTITY_ID", "ETERNITAS_URL", "ETERNITAS_API_URL",
        "ETERNITAS_PASSPORT", "OWNER_EMAIL",
    ):
        monkeypatch.delenv(k, raising=False)


# ────────────────────────────────────────────────────────────────────
# P1-E3
# ────────────────────────────────────────────────────────────────────


class TestP1E3SharedJwks:
    @respx.mock
    async def test_both_ok_when_jwks_shared(self, monkeypatch):
        monkeypatch.setenv("WINDY_PRO_URL", PRO)
        monkeypatch.setenv("WINDY_CLOUD_URL", CLOUD)
        monkeypatch.setenv("WINDY_JWT", "owner_jwt")

        respx.post(f"{PRO}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.post(f"{CLOUD}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(200, json={})
        )

        summary = await link_passport_with_identity(
            passport_number="ET26-X",
            windy_identity_id="wi_1",
        )
        assert summary == {"pro": "linked", "cloud": "linked"}

    @respx.mock
    async def test_half_linked_state_surfaces_per_service(self, monkeypatch):
        """Cloud 401 + Pro 200 → summary reports both, no raise."""
        monkeypatch.setenv("WINDY_PRO_URL", PRO)
        monkeypatch.setenv("WINDY_CLOUD_URL", CLOUD)
        monkeypatch.setenv("WINDY_JWT", "owner_jwt")

        respx.post(f"{PRO}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(200, json={})
        )
        # Cloud rejects — simulates a diverged JWKS.
        respx.post(f"{CLOUD}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(401, text="invalid token")
        )

        summary = await link_passport_with_identity(
            passport_number="ET26-X",
            windy_identity_id="wi_1",
        )
        assert summary["pro"] == "linked"
        assert summary["cloud"] == "http_401"

    @respx.mock
    async def test_same_bearer_sent_to_both(self, monkeypatch):
        """The Bearer is literally identical on both calls — the
        coupling is explicit."""
        monkeypatch.setenv("WINDY_PRO_URL", PRO)
        monkeypatch.setenv("WINDY_CLOUD_URL", CLOUD)
        monkeypatch.setenv("WINDY_JWT", "owner_jwt_shared")

        pro = respx.post(f"{PRO}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(200, json={})
        )
        cloud = respx.post(f"{CLOUD}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(200, json={})
        )

        await link_passport_with_identity(
            passport_number="ET26-X",
            windy_identity_id="wi_1",
        )

        pro_auth = pro.calls.last.request.headers.get("Authorization")
        cloud_auth = cloud.calls.last.request.headers.get("Authorization")
        assert pro_auth == cloud_auth == "Bearer owner_jwt_shared"


# ────────────────────────────────────────────────────────────────────
# P1-E4
# ────────────────────────────────────────────────────────────────────


class TestP1E4MintBotKey:
    async def test_mint_step_skips_without_jwt(self):
        """Offline hatch (no JWT) → mint step is a silent no-op."""
        result = HatchResult(passport_id="ET26-X")
        await _step_mint_bot_key(result)
        assert result.errors == []

    async def test_mint_step_skips_without_passport(self):
        """No passport → nothing to mint against."""
        result = HatchResult(passport_id="")
        await _step_mint_bot_key(result)
        assert result.errors == []

    @respx.mock
    async def test_mint_step_happy_path(self, monkeypatch, tmp_path):
        from windyfly.auth import bot_credentials
        monkeypatch.setattr(bot_credentials, "_CACHE_FILE", tmp_path / "key.json")
        monkeypatch.setenv("WINDY_PRO_URL", PRO)
        monkeypatch.setenv("WINDY_JWT", "owner_jwt")
        respx.post(f"{PRO}/api/v1/identity/bot-keys/mint").mock(
            return_value=httpx.Response(200, json={
                "bot_key": "wk_minted_in_hatch",
                "expires_at": "2027-04-16T00:00:00Z",
                "key_id": "wbk_h_1",
                "scopes": ["cloud:upload", "mail:send"],
            })
        )
        result = HatchResult(passport_id="ET26-X")

        await _step_mint_bot_key(result)

        cached = bot_credentials._load_cached()
        assert cached is not None
        assert cached.bot_key == "wk_minted_in_hatch"
        assert "cloud:upload" in cached.scopes
        assert result.errors == []

    @respx.mock
    async def test_cloud_quota_uses_wk_key_when_minted(self, monkeypatch, tmp_path):
        """After mint_bot_key, cloud_quota sends the wk_ key as
        Bearer, not the owner JWT."""
        from windyfly.auth import bot_credentials
        from datetime import datetime, timedelta, timezone
        monkeypatch.setattr(bot_credentials, "_CACHE_FILE", tmp_path / "key.json")
        monkeypatch.setenv("WINDY_CLOUD_URL", CLOUD)
        monkeypatch.setenv("WINDY_JWT", "owner_jwt_fallback")

        # Pre-populate the cache as if step 1c already ran.
        bot_credentials._save_cached(bot_credentials.BotCredential(
            bot_key="wk_from_mint",
            expires_at=datetime.now(timezone.utc) + timedelta(days=90),
            passport_number="ET26-X",
            scopes=["cloud:upload"],
        ))

        route = respx.post(f"{CLOUD}/api/v1/billing/allocate").mock(
            return_value=httpx.Response(200, json={
                "plan_id": "cp_free", "quota_bytes": 1_000_000, "tier": "free",
            })
        )

        result = HatchResult(passport_id="ET26-X")
        await _step_cloud_quota(result, owner_id="owner-x")

        auth = route.calls.last.request.headers.get("Authorization")
        assert auth == "Bearer wk_from_mint", \
            f"Expected wk_ key, got {auth!r}"
        assert result.cloud_provisioned

    @respx.mock
    async def test_cloud_quota_falls_back_to_owner_jwt(self, monkeypatch, tmp_path):
        """Without a cached wk_ key, cloud_quota still works on the
        owner JWT (cloud_provision's own fallback)."""
        from windyfly.auth import bot_credentials
        monkeypatch.setattr(bot_credentials, "_CACHE_FILE", tmp_path / "key.json")
        bot_credentials.clear_cached_bot_key()

        monkeypatch.setenv("WINDY_CLOUD_URL", CLOUD)
        monkeypatch.setenv("WINDY_JWT", "owner_jwt_only")

        route = respx.post(f"{CLOUD}/api/v1/billing/allocate").mock(
            return_value=httpx.Response(200, json={
                "plan_id": "cp_free", "quota_bytes": 1, "tier": "free",
            })
        )

        result = HatchResult(passport_id="ET26-X")
        await _step_cloud_quota(result, owner_id="owner-x")

        auth = route.calls.last.request.headers.get("Authorization")
        assert auth == "Bearer owner_jwt_only"
        assert result.cloud_provisioned
