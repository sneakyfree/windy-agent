"""Contract tests for Windy Cloud storage quota allocation.

Covers POST /api/v1/billing/allocate — the hatch step that gives
every newly born agent a cloud home.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from windyfly.cloud_provision import allocate_cloud_quota

CLOUD_BASE = "https://cloud.windy.test"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WINDY_CLOUD_URL", CLOUD_BASE)
    monkeypatch.setenv("WINDY_CLOUD_TOKEN", "cloud_service_token")
    monkeypatch.delenv("WINDY_JWT", raising=False)


class TestCloudQuotaContract:
    @respx.mock
    async def test_posts_correct_body(self):
        route = respx.post(f"{CLOUD_BASE}/api/v1/billing/allocate").mock(
            return_value=httpx.Response(200, json={
                "plan_id": "cp_free_42",
                "quota_bytes": 5 * 1024 * 1024 * 1024,
                "tier": "free",
            })
        )

        alloc = await allocate_cloud_quota(
            windy_identity_id="wi_user_1",
            passport_number="ET-00042",
        )

        assert route.called
        body = route.calls.last.request.content.decode()
        assert "wi_user_1" in body
        assert "ET-00042" in body
        assert '"tier":"free"' in body or '"tier": "free"' in body
        assert alloc is not None
        assert alloc.plan_id == "cp_free_42"
        assert alloc.quota_bytes == 5 * 1024 * 1024 * 1024
        assert alloc.tier == "free"

    @respx.mock
    async def test_sends_bearer_auth(self):
        route = respx.post(f"{CLOUD_BASE}/api/v1/billing/allocate").mock(
            return_value=httpx.Response(200, json={"plan_id": "p", "quota_bytes": 1})
        )

        await allocate_cloud_quota(
            windy_identity_id="wi_1",
            passport_number="ET-1",
        )

        assert route.calls.last.request.headers["Authorization"] == "Bearer cloud_service_token"

    async def test_skips_when_url_unset(self, monkeypatch):
        monkeypatch.delenv("WINDY_CLOUD_URL", raising=False)
        alloc = await allocate_cloud_quota(
            windy_identity_id="wi_1",
            passport_number="ET-1",
        )
        assert alloc is None

    @respx.mock
    async def test_returns_none_on_server_error(self):
        respx.post(f"{CLOUD_BASE}/api/v1/billing/allocate").mock(
            return_value=httpx.Response(500, text="boom")
        )

        alloc = await allocate_cloud_quota(
            windy_identity_id="wi_1",
            passport_number="ET-1",
        )
        assert alloc is None

    @respx.mock
    async def test_returns_none_on_connection_error(self):
        respx.post(f"{CLOUD_BASE}/api/v1/billing/allocate").mock(
            side_effect=httpx.ConnectError("down")
        )

        alloc = await allocate_cloud_quota(
            windy_identity_id="wi_1",
            passport_number="ET-1",
        )
        assert alloc is None

    @respx.mock
    async def test_bot_key_takes_precedence_over_env_token(self):
        route = respx.post(f"{CLOUD_BASE}/api/v1/billing/allocate").mock(
            return_value=httpx.Response(200, json={"plan_id": "p", "quota_bytes": 1})
        )

        await allocate_cloud_quota(
            windy_identity_id="wi_1",
            passport_number="ET-1",
            bot_key="wk_abc123",
        )

        assert route.calls.last.request.headers["Authorization"] == "Bearer wk_abc123"
