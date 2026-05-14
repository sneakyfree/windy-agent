"""Regression: setup_status counts Resend as configured email.

Surfaced 2026-05-14: PR #177 wired Resend as a send-email fallback,
verified it works end-to-end (real test email delivered via Resend API,
HTTP 200). But the bot's first in-process smoke probe with the
Austin-TX-mortgage prompt got: "my email sending feature isn't connected
yet" — the LLM avoided the send_email tool and routed to the OAuth
setup wizard.

Root cause: ``setup_status._gmail_configured()`` only checked the Gmail
token file. With no Gmail token but Resend wired, the integration was
reported as ``configured=false``. The LLM read that, concluded email
was dormant, suggested ``set up email`` instead of just sending.

This test pins the contract: when EITHER Gmail OAuth OR Resend is
wired, the "email-send capable" signal goes True.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from windyfly.agent.setup_status import (
    _gmail_configured,
    _resend_configured,
    get_setup_status,
)


class TestResendConfigured:

    def test_both_resend_vars_set(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")
        assert _resend_configured() is True

    def test_key_only(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.delenv("RESEND_FROM_ADDRESS", raising=False)
        assert _resend_configured() is False

    def test_from_only(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")
        assert _resend_configured() is False

    def test_neither(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.delenv("RESEND_FROM_ADDRESS", raising=False)
        assert _resend_configured() is False


class TestGmailConfiguredCountsResend:
    """``_gmail_configured()`` is the signal the LLM reads via
    setup_status. It must return True when EITHER backend is wired."""

    def test_resend_alone_makes_gmail_configured_true(
        self, monkeypatch, tmp_path
    ):
        # Point GMAIL_TOKEN at a path that does NOT exist
        ghost = tmp_path / "no_gmail_token.json"
        monkeypatch.setenv("GMAIL_TOKEN", str(ghost))
        assert not ghost.exists()

        # Wire Resend
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        assert _gmail_configured() is True, (
            "When Resend is the wired backend, the email-send signal "
            "must be True — otherwise the LLM sees email as dormant "
            "and routes to the setup wizard instead of using send_email."
        )

    def test_gmail_token_alone_works(self, monkeypatch, tmp_path):
        token = tmp_path / "gmail_token.json"
        token.write_text('{"access_token": "fake"}')
        monkeypatch.setenv("GMAIL_TOKEN", str(token))
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.delenv("RESEND_FROM_ADDRESS", raising=False)
        assert _gmail_configured() is True

    def test_neither_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GMAIL_TOKEN", str(tmp_path / "ghost.json"))
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.delenv("RESEND_FROM_ADDRESS", raising=False)
        assert _gmail_configured() is False


class TestSetupStatusReport:
    """End-to-end: the LLM-visible setup status report reflects Resend."""

    def test_resend_wiring_makes_email_integration_configured(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("GMAIL_TOKEN", str(tmp_path / "ghost.json"))
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        status = get_setup_status()
        # Find the gmail integration (which now covers Resend too)
        email = next(
            i for i in status["integrations"] if i["key"] == "gmail"
        )
        assert email["configured"] is True
        # Name should signal the new dual-backend reality
        assert (
            "Resend" in email["name"] or "Resend" in email.get("note", "")
        ), "integration name/note should mention Resend so the LLM knows"

    def test_resend_keeps_gmail_out_of_dormant_keys(
        self, monkeypatch, tmp_path
    ):
        """The ``dormant_keys`` list is the LLM's "needs setup" view.
        When Resend is wired, gmail shouldn't show up there — that's
        what causes the LLM to suggest the setup wizard."""
        monkeypatch.setenv("GMAIL_TOKEN", str(tmp_path / "ghost.json"))
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        status = get_setup_status()
        assert "gmail" not in status.get("dormant_keys", []), (
            f"gmail should NOT be dormant when Resend is wired; "
            f"dormant_keys={status.get('dormant_keys')}"
        )
