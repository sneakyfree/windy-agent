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
    async def test_pro_404_is_reported_as_route_absent(self, caplog):
        """windy-pro has NO /api/v1/identity/link-passport route (verified
        2026-08-10 against account-server), so the Pro leg 404s on every
        hatch. That must read as its own state — never as 'linked', never
        as a transport error — and must be logged loudly."""
        import logging

        respx.post(f"{PRO_BASE}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(404, text="Not Found")
        )
        respx.post(f"{CLOUD_BASE}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(200, json={"status": "linked"})
        )

        with caplog.at_level(logging.WARNING, logger="windyfly.eternitas.provision"):
            summary = await link_passport_with_identity(
                passport_number="ET-1",
                windy_identity_id="wi_1",
            )

        assert summary == {"pro": "route_absent", "cloud": "linked"}
        assert any(
            "no /api/v1/identity/link-passport route" in r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        )

    @respx.mock
    async def test_cloud_404_stays_a_generic_http_error(self):
        """Cloud DOES serve the route, so a 404 there is a real surprise —
        it must not be excused as the known Pro-side contract gap."""
        respx.post(f"{PRO_BASE}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.post(f"{CLOUD_BASE}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        summary = await link_passport_with_identity(
            passport_number="ET-1",
            windy_identity_id="wi_1",
        )

        assert summary == {"pro": "linked", "cloud": "http_404"}

    @respx.mock
    async def test_pro_transport_error_is_not_route_absent(self):
        """A dead network is a different fact from a missing route."""
        respx.post(f"{PRO_BASE}/api/v1/identity/link-passport").mock(
            side_effect=httpx.ConnectError("no route to host")
        )
        respx.post(f"{CLOUD_BASE}/api/v1/identity/link-passport").mock(
            return_value=httpx.Response(200, json={})
        )

        summary = await link_passport_with_identity(
            passport_number="ET-1",
            windy_identity_id="wi_1",
        )

        assert summary["pro"] == "error: ConnectError"
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
