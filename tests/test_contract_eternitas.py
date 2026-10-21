"""Contract tests for Eternitas API integration.

Verifies the EternitasClient sends correct headers, paths, and body,
and correctly parses the Eternitas API response shape into an
EternitasPassport dataclass.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from windyfly.eternitas.client import EternitasClient

ETERNITAS_BASE = "https://api.eternitas.test"
OPERATOR_KEY = "et_op_test_key_12345"
ADMIN_TOKEN = "admin_jwt_token_12345"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ETERNITAS_ADMIN_TOKEN", ADMIN_TOKEN)
    return EternitasClient(api_url=ETERNITAS_BASE, operator_key=OPERATOR_KEY)


class TestVerifyContract:
    @respx.mock
    async def test_verify_uses_correct_path(self, client):
        """GET /api/v1/registry/verify/{passport} — public, no auth."""
        route = respx.get(f"{ETERNITAS_BASE}/api/v1/registry/verify/ET-00001").mock(
            return_value=httpx.Response(200, json={
                "passport": "ET-00001",
                "name": "test-fly",
                "ept_token": "",
                "api_key": "",
                "status": "active",
                "trust_score": 70,
            })
        )

        result = await client.verify("ET-00001")

        assert route.called
        request = route.calls.last.request
        # No auth header — public endpoint
        assert "Authorization" not in request.headers
        assert "X-API-Key" not in request.headers
        assert result is not None
        assert result.passport_id == "ET-00001"

    @respx.mock
    async def test_verify_404_returns_none(self, client):
        """Non-existent passport returns None."""
        respx.get(f"{ETERNITAS_BASE}/api/v1/registry/verify/ET-99999").mock(
            return_value=httpx.Response(404)
        )

        result = await client.verify("ET-99999")
        assert result is None


# --- Revoke contract ---


class TestRevokeContract:
    @respx.mock
    async def test_revoke_uses_admin_path_and_bearer(self, client):
        """POST /api/v1/admin/revoke/{passport} with Bearer admin token."""
        route = respx.post(f"{ETERNITAS_BASE}/api/v1/admin/revoke/ET-00001").mock(
            return_value=httpx.Response(200, json={
                "passport_id": "ET-00001",
                "revoked": True,
                "services_torn_down": ["matrix", "mail"],
            })
        )

        result = await client.revoke("ET-00001", reason="test revocation")

        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == f"Bearer {ADMIN_TOKEN}"

        import json
        body = json.loads(request.content)
        assert body["reason"] == "test revocation"

        assert result.revoked is True
        assert set(result.services_torn_down) == {"matrix", "mail"}

    @respx.mock
    async def test_revoke_connection_error_returns_result(self, client):
        """Connection error returns a RevocationResult with error, not crash."""
        respx.post(f"{ETERNITAS_BASE}/api/v1/admin/revoke/ET-00001").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        result = await client.revoke("ET-00001")
        assert result.revoked is False
        assert result.error != ""
