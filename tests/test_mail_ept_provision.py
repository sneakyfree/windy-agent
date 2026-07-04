"""Client-side EPT-gated mail provisioning + tool disambiguation (Sprint 5).

The agent now provisions its mailbox by presenting its own EPT (windy-mail
PR #62), and the Gmail send capability only registers when Gmail is
actually connected — so a keyless agent sees only its Windy Mail inbox.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly import mail_provision
from windyfly.agent.capabilities.email import register_email_capabilities
from windyfly.agent.capabilities.registry import CapabilityRegistry


PROVISION_OK = {
    "email": "testbot@windymail.ai",
    "jmap_token": "jmap-tok-123",
    "jmap_url": "https://mail.windymail.ai/.well-known/jmap",
}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_mail_env(monkeypatch, tmp_path):
    for var in (
        "ETERNITAS_PASSPORT_TOKEN", "WINDYMAIL_PROVISION_SERVICE_TOKEN",
        "WINDYMAIL_SERVICE_TOKEN", "WINDYMAIL_API_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # _write_env writes to cwd/.env


class TestProvisionAuth:
    def _post_mock(self):
        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = PROVISION_OK
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx, client

    def test_ept_used_as_bearer(self, monkeypatch):
        monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-jwt-xyz")
        ctx, client = self._post_mock()
        with patch("httpx.AsyncClient", return_value=ctx):
            out = _run(mail_provision.provision_mail(
                "TestBot", "ET26-X", "owner1",
            ))
        assert out["email"] == "testbot@windymail.ai"
        headers = client.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer ept-jwt-xyz"
        assert "X-Service-Token" not in headers

    def test_service_token_fallback_when_no_ept(self, monkeypatch):
        monkeypatch.setenv("WINDYMAIL_PROVISION_SERVICE_TOKEN", "svc-tok")
        ctx, client = self._post_mock()
        with patch("httpx.AsyncClient", return_value=ctx):
            out = _run(mail_provision.provision_mail("B", "ET26-Y", "o"))
        assert out is not None
        headers = client.post.call_args.kwargs["headers"]
        assert headers["X-Service-Token"] == "svc-tok"
        assert "Authorization" not in headers

    def test_ept_preferred_over_service_token(self, monkeypatch):
        monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-win")
        monkeypatch.setenv("WINDYMAIL_PROVISION_SERVICE_TOKEN", "svc-lose")
        ctx, client = self._post_mock()
        with patch("httpx.AsyncClient", return_value=ctx):
            _run(mail_provision.provision_mail("B", "ET26-Z", "o"))
        headers = client.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer ept-win"

    def test_no_credentials_skips_gracefully(self):
        # No EPT, no service token → skip (returns None), no network.
        with patch("httpx.AsyncClient") as mock_client:
            out = _run(mail_provision.provision_mail("B", "ET26-N", "o"))
        assert out is None
        mock_client.assert_not_called()


class TestEmailCapabilityGating:
    def test_not_registered_when_no_backend(self, monkeypatch):
        # No Gmail, no Resend → email.send is a dead stub → don't register.
        from windyfly.agent.capabilities import email as email_mod

        monkeypatch.setattr(email_mod, "_is_configured", lambda: False)
        monkeypatch.setattr(
            "windyfly.tools.mail._resend_configured", lambda: False,
        )
        reg = CapabilityRegistry()
        email_mod.register_email_capabilities(reg)
        assert reg.get("email.send") is None

    def test_registered_when_gmail_present(self, monkeypatch):
        from windyfly.agent.capabilities import email as email_mod

        monkeypatch.setattr(email_mod, "_is_configured", lambda: True)
        reg = CapabilityRegistry()
        email_mod.register_email_capabilities(reg)
        assert reg.get("email.send") is not None

    def test_registered_when_resend_present(self, monkeypatch):
        from windyfly.agent.capabilities import email as email_mod

        monkeypatch.setattr(email_mod, "_is_configured", lambda: False)
        monkeypatch.setattr(
            "windyfly.tools.mail._resend_configured", lambda: True,
        )
        reg = CapabilityRegistry()
        email_mod.register_email_capabilities(reg)
        assert reg.get("email.send") is not None
