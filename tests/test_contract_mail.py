"""Contract tests for Windy Mail provisioning integration.

Verifies that provision_mail() sends the correct request format
to POST /api/v1/provision/bot and correctly parses the response.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from windyfly.mail_provision import provision_mail

MAIL_BASE = "https://api.windymail.test"
SERVICE_TOKEN = "svc_token_test_12345"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch, tmp_path):
    """Configure mail provisioning env vars and prevent .env writes."""
    monkeypatch.setenv("WINDYMAIL_API_URL", MAIL_BASE)
    monkeypatch.setenv("WINDYMAIL_PROVISION_SERVICE_TOKEN", SERVICE_TOKEN)
    # Use a temp dir so _write_env doesn't touch the real .env
    monkeypatch.chdir(tmp_path)


class TestMailProvisioningContract:
    @respx.mock
    async def test_sends_correct_path_and_method(self):
        """POST /api/v1/provision/bot."""
        route = respx.post(f"{MAIL_BASE}/api/v1/provision/bot").mock(
            return_value=httpx.Response(201, json={
                "email": "test-fly@windymail.ai",
                "smtp_password": "smtp_pass_123",
                "imap_password": "imap_pass_456",
                "jmap_token": "jmap_tok_789",
                "jmap_url": "https://mail.windymail.ai/.well-known/jmap",
            })
        )

        await provision_mail("test-fly", "ET-00001", "owner-1", "windy-id-1")
        assert route.called

    @respx.mock
    async def test_sends_x_service_token_header(self):
        """Request uses X-Service-Token header."""
        route = respx.post(f"{MAIL_BASE}/api/v1/provision/bot").mock(
            return_value=httpx.Response(201, json={
                "email": "test-fly@windymail.ai",
                "smtp_password": "sp",
                "imap_password": "ip",
                "jmap_token": "jt",
            })
        )

        await provision_mail("test-fly", "ET-00001", "owner-1")

        request = route.calls.last.request
        assert request.headers["X-Service-Token"] == SERVICE_TOKEN
        # Must NOT use Bearer auth
        assert "Authorization" not in request.headers

    @respx.mock
    async def test_sends_correct_body_fields(self):
        """Body includes eternitas_passport, agent_name, owner_id, windy_identity_id."""
        route = respx.post(f"{MAIL_BASE}/api/v1/provision/bot").mock(
            return_value=httpx.Response(201, json={
                "email": "test-fly@windymail.ai",
                "smtp_password": "sp",
                "imap_password": "ip",
                "jmap_token": "jt",
            })
        )

        await provision_mail(
            agent_name="test-fly",
            eternitas_passport="ET-00001",
            owner_id="owner-1",
            windy_identity_id="windy-id-42",
        )

        import json
        body = json.loads(route.calls.last.request.content)
        assert body["eternitas_passport"] == "ET-00001"
        assert body["agent_name"] == "test-fly"
        assert body["owner_id"] == "owner-1"
        assert body["windy_identity_id"] == "windy-id-42"

    @respx.mock
    async def test_windy_identity_id_defaults_to_owner_id(self):
        """When windy_identity_id is not provided, falls back to owner_id."""
        route = respx.post(f"{MAIL_BASE}/api/v1/provision/bot").mock(
            return_value=httpx.Response(201, json={
                "email": "test-fly@windymail.ai",
                "smtp_password": "sp",
                "imap_password": "ip",
                "jmap_token": "jt",
            })
        )

        await provision_mail("test-fly", "ET-00001", "owner-1")

        import json
        body = json.loads(route.calls.last.request.content)
        assert body["windy_identity_id"] == "owner-1"

    @respx.mock
    async def test_parses_response_credentials(self):
        """Response with email, smtp_password, imap_password, jmap_token is returned."""
        respx.post(f"{MAIL_BASE}/api/v1/provision/bot").mock(
            return_value=httpx.Response(201, json={
                "email": "fly@windymail.ai",
                "smtp_password": "smtp_secret",
                "imap_password": "imap_secret",
                "jmap_token": "jmap_tok_abc",
                "jmap_url": "https://mail.windymail.ai/.well-known/jmap",
            })
        )

        result = await provision_mail("fly", "ET-00001", "owner-1")

        assert result is not None
        assert result["email"] == "fly@windymail.ai"
        assert result["smtp_password"] == "smtp_secret"
        assert result["imap_password"] == "imap_secret"
        assert result["jmap_token"] == "jmap_tok_abc"

    @respx.mock
    async def test_writes_env_keys(self, tmp_path):
        """Successful provisioning writes credentials to .env."""
        respx.post(f"{MAIL_BASE}/api/v1/provision/bot").mock(
            return_value=httpx.Response(201, json={
                "email": "fly@windymail.ai",
                "smtp_password": "smtp_x",
                "imap_password": "imap_x",
                "jmap_token": "jt_x",
            })
        )

        await provision_mail("fly", "ET-00001", "owner-1")

        env_file = tmp_path / ".env"
        env_content = env_file.read_text(encoding="utf-8")
        assert "WINDYMAIL_EMAIL=fly@windymail.ai" in env_content
        assert "WINDYMAIL_JMAP_TOKEN=jt_x" in env_content
        assert "WINDYMAIL_SMTP_PASSWORD=smtp_x" in env_content
        assert "WINDYMAIL_IMAP_PASSWORD=imap_x" in env_content

    @respx.mock
    async def test_http_error_returns_none(self):
        """Non-success status returns None without crashing."""
        respx.post(f"{MAIL_BASE}/api/v1/provision/bot").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        result = await provision_mail("fly", "ET-00001", "owner-1")
        assert result is None

    @respx.mock
    async def test_connection_error_returns_none(self):
        """Connection failure returns None with friendly logging."""
        respx.post(f"{MAIL_BASE}/api/v1/provision/bot").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        result = await provision_mail("fly", "ET-00001", "owner-1")
        assert result is None

    async def test_no_service_token_skips(self, monkeypatch):
        """Without WINDYMAIL_PROVISION_SERVICE_TOKEN, returns None immediately."""
        monkeypatch.delenv("WINDYMAIL_PROVISION_SERVICE_TOKEN", raising=False)
        monkeypatch.delenv("WINDYMAIL_SERVICE_TOKEN", raising=False)

        result = await provision_mail("fly", "ET-00001", "owner-1")
        assert result is None
