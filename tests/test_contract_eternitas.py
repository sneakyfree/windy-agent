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
from windyfly.eternitas.models import EternitasPassport, RegistrationRequest

ETERNITAS_BASE = "https://api.eternitas.test"
OPERATOR_KEY = "et_op_test_key_12345"
ADMIN_TOKEN = "admin_jwt_token_12345"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ETERNITAS_ADMIN_TOKEN", ADMIN_TOKEN)
    return EternitasClient(api_url=ETERNITAS_BASE, operator_key=OPERATOR_KEY)


@pytest.fixture
def registration_request():
    return RegistrationRequest(
        name="test-fly",
        description="A test agent",
        bot_type="personal_assistant",
        contact_email="owner@example.com",
        intended_platforms=["windy_chat", "windy_mail"],
    )


# --- Registration contract ---


class TestRegistrationContract:
    @respx.mock
    async def test_sends_correct_path_and_method(self, client, registration_request):
        """POST /api/v1/bots/register with correct path."""
        route = respx.post(f"{ETERNITAS_BASE}/api/v1/bots/register").mock(
            return_value=httpx.Response(200, json={
                "passport": "ET-00001",
                "name": "test-fly",
                "ept_token": "jwt_token_123",
                "api_key": "et_live_abc123",
                "status": "active",
                "trust_score": 70,
            })
        )

        await client.register(registration_request)
        assert route.called

    @respx.mock
    async def test_sends_x_api_key_header(self, client, registration_request):
        """Registration uses X-API-Key header with operator key, NOT Bearer."""
        route = respx.post(f"{ETERNITAS_BASE}/api/v1/bots/register").mock(
            return_value=httpx.Response(200, json={
                "passport": "ET-00001",
                "name": "test-fly",
                "ept_token": "jwt",
                "api_key": "et_live_x",
                "status": "active",
                "trust_score": 70,
            })
        )

        await client.register(registration_request)

        request = route.calls.last.request
        assert request.headers["X-API-Key"] == OPERATOR_KEY
        assert "Authorization" not in request.headers

    @respx.mock
    async def test_sends_correct_body_fields(self, client, registration_request):
        """Request body must contain name, description, bot_type, contact_email, intended_platforms."""
        route = respx.post(f"{ETERNITAS_BASE}/api/v1/bots/register").mock(
            return_value=httpx.Response(200, json={
                "passport": "ET-00001",
                "name": "test-fly",
                "ept_token": "jwt",
                "api_key": "et_live_x",
                "status": "active",
                "trust_score": 70,
            })
        )

        await client.register(registration_request)

        import json
        body = json.loads(route.calls.last.request.content)
        assert body["name"] == "test-fly"
        assert body["description"] == "A test agent"
        assert body["bot_type"] == "personal_assistant"
        assert body["contact_email"] == "owner@example.com"
        assert body["intended_platforms"] == ["windy_chat", "windy_mail"]
        # Internal fields must NOT leak to the API
        assert "owner_id" not in body
        assert "model_id" not in body
        assert "hatch_machine_id" not in body

    @respx.mock
    async def test_parses_response_into_passport(self, client, registration_request):
        """Response is correctly parsed into an EternitasPassport."""
        respx.post(f"{ETERNITAS_BASE}/api/v1/bots/register").mock(
            return_value=httpx.Response(200, json={
                "passport": "ET-00001",
                "name": "test-fly",
                "ept_token": "jwt_token_xyz",
                "api_key": "et_live_abc999",
                "status": "active",
                "trust_score": 85,
            })
        )

        passport = await client.register(registration_request)

        assert isinstance(passport, EternitasPassport)
        assert passport.passport_id == "ET-00001"
        assert passport.name == "test-fly"
        assert passport.agent_name == "test-fly"  # backward compat property
        assert passport.ept_token == "jwt_token_xyz"
        assert passport.api_key == "et_live_abc999"
        assert passport.status == "active"
        assert passport.trust_score == 85

    @respx.mock
    async def test_registration_http_error_raises(self, client, registration_request):
        """Non-200 response raises HTTPStatusError."""
        respx.post(f"{ETERNITAS_BASE}/api/v1/bots/register").mock(
            return_value=httpx.Response(422, json={"error": "invalid request"})
        )

        with pytest.raises(httpx.HTTPStatusError):
            await client.register(registration_request)


# --- Verify contract ---


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
