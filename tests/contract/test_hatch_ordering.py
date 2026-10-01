"""Contract tests for P1-E3 (shared-JWKS coupling).

P1-E3 — link_passport_with_identity sends the same owner JWT as
Bearer to both Windy Pro and Windy Cloud. This works only because
both services validate against a shared JWKS. The test proves that
a half-linked state (one 200, one 401) surfaces as a per-service
status in the summary dict, not a global failure.

(P1-E4 covered the terminal hatch orchestrator's step order; that
orchestrator was removed in 0.7.5 — ADR-059, one hallway.)
"""

from __future__ import annotations


import httpx
import pytest
import respx

from windyfly.eternitas.provision import link_passport_with_identity


PRO = "https://pro.windy.test"
CLOUD = "https://cloud.windy.test"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for k in (
        "WINDY_PRO_URL", "WINDY_API_URL", "WINDY_CLOUD_URL", "WINDY_JWT",
        "WINDY_IDENTITY_ID", "ETERNITAS_URL", "ETERNITAS_API_URL",
        "ETERNITAS_PASSPORT", "OWNER_EMAIL", "BOT_IDENTITY_ID",
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
