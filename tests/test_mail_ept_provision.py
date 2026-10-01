"""Mail tool disambiguation (Sprint 5).

The Gmail send capability only registers when Gmail is actually
connected — so a keyless agent sees only its Windy Mail inbox. (The
CLI-side mailbox provisioning these tests used to cover was removed in
0.7.5 with the terminal hatch — ADR-059, one hallway.)
"""

from __future__ import annotations

import pytest

from windyfly.agent.capabilities.registry import CapabilityRegistry


@pytest.fixture(autouse=True)
def _clean_mail_env(monkeypatch, tmp_path):
    for var in (
        "ETERNITAS_PASSPORT_TOKEN", "WINDYMAIL_PROVISION_SERVICE_TOKEN",
        "WINDYMAIL_SERVICE_TOKEN", "WINDYMAIL_API_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # _write_env writes to cwd/.env


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
