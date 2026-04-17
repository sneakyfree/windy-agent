"""Contract tests for passport ↔ identity link-back.

Covers POST /api/v1/identity/link-passport on both Windy Pro and
Windy Cloud, and the offline/standalone skip behaviour.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from windyfly.eternitas.provision import link_passport_with_identity

PRO_BASE = "https://pro.windy.test"
CLOUD_BASE = "https://cloud.windy.test"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WINDY_PRO_URL", PRO_BASE)
    monkeypatch.setenv("WINDY_CLOUD_URL", CLOUD_BASE)
    monkeypatch.setenv("WINDY_JWT", "owner_jwt_abc")
    monkeypatch.setenv("OWNER_EMAIL", "grant@example.com")


class TestLinkPassportContract:
    @respx.mock
    async def test_posts_to_both_services_with_correct_body(self):
        pro = respx.post(f"{PRO_BASE}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(200, json={"status": "linked"})
        )
        cloud = respx.post(f"{CLOUD_BASE}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(200, json={"status": "linked"})
        )

        summary = await link_passport_with_identity(
            passport_number="ET-00042",
            windy_identity_id="wi_user_1",
        )

        assert pro.called and cloud.called
        body = pro.calls.last.request.content.decode()
        assert "ET-00042" in body
        assert "wi_user_1" in body
        assert "grant@example.com" in body
        assert summary == {"pro": "linked", "cloud": "linked"}

    @respx.mock
    async def test_sends_bearer_jwt(self):
        pro = respx.post(f"{PRO_BASE}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(204)
        )
        respx.post(f"{CLOUD_BASE}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(204)
        )

        await link_passport_with_identity(
            passport_number="ET-1",
            windy_identity_id="wi_1",
        )

        assert pro.calls.last.request.headers["Authorization"] == "Bearer owner_jwt_abc"

    async def test_skips_gracefully_without_identity(self, monkeypatch):
        # Offline/standalone: no windy_identity_id — must not make any HTTP call.
        with respx.mock(assert_all_called=False) as mock:
            pro = mock.post(f"{PRO_BASE}/api/v1/identity/link-passport")
            cloud = mock.post(f"{CLOUD_BASE}/api/v1/identity/link-passport")

            summary = await link_passport_with_identity(
                passport_number="ET-1",
                windy_identity_id="",
            )

        assert not pro.called and not cloud.called
        assert summary == {"pro": "skipped", "cloud": "skipped"}

    @respx.mock
    async def test_reports_error_per_service_independently(self):
        respx.post(f"{PRO_BASE}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(500, text="boom")
        )
        respx.post(f"{CLOUD_BASE}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(201, json={})
        )

        summary = await link_passport_with_identity(
            passport_number="ET-1",
            windy_identity_id="wi_1",
        )

        assert summary["pro"] == "http_500"
        assert summary["cloud"] == "linked"

    @respx.mock
    async def test_skips_service_when_its_url_unset(self, monkeypatch):
        monkeypatch.delenv("WINDY_CLOUD_URL", raising=False)
        pro = respx.post(f"{PRO_BASE}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(200, json={})
        )

        summary = await link_passport_with_identity(
            passport_number="ET-1",
            windy_identity_id="wi_1",
        )

        assert pro.called
        assert summary == {"pro": "linked", "cloud": "skipped"}
