"""Regression: email.send capability falls through to Resend.

Surfaced 2026-05-14: PR #177 wired Resend as a send-email fallback
in ``tools/mail.py``, and PR #178 taught ``setup_status`` to count
Resend as configured. End-to-end smoke STILL showed the bot saying
"my email connection isn't set up yet" — because the LLM was calling
the ``email.send`` CAPABILITY (which has its own _is_configured()
check tied strictly to the Gmail token file), not the tools/mail.py
function.

This test pins the contract: when Gmail OAuth isn't wired but
Resend env IS, the email.send capability calls through to Resend
and returns ``executed=True`` with a message_id, instead of the
``dormant_integration`` shape that steers the LLM to the wizard.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from windyfly.agent.capabilities.email import _send_email_handler


class TestEmailCapabilityResendFallback:

    def test_resend_when_gmail_token_missing(self, monkeypatch, tmp_path):
        # Gmail token doesn't exist
        monkeypatch.setenv("GMAIL_TOKEN", str(tmp_path / "no-gmail.json"))
        # Resend IS wired
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        def fake_post(url, headers=None, json=None, timeout=None):
            class R:
                status_code = 200
                text = '{"id":"rs-123"}'
                def json(self_inner):
                    return {"id": "rs-123"}
            return R()

        with patch("httpx.post", side_effect=fake_post):
            r = _send_email_handler(
                to="dest@x.com", subject="hello", body="body text",
            )

        assert r["executed"] is True, (
            "Resend is wired and Gmail isn't — capability must send "
            "via Resend, not return dormant_integration"
        )
        assert r["provider"] == "resend"
        assert r["message_id"] == "rs-123"
        # The "plan" shape still has the header-checked metadata
        assert r["plan"]["to"] == "dest@x.com"
        assert r["plan"]["subject"] == "hello"

    def test_dormant_when_neither_wired(self, monkeypatch, tmp_path):
        """The original dormant-integration path stays intact when
        NEITHER backend is wired — the LLM still gets the setup nudge."""
        monkeypatch.setenv("GMAIL_TOKEN", str(tmp_path / "no-gmail.json"))
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.delenv("RESEND_FROM_ADDRESS", raising=False)

        r = _send_email_handler(
            to="dest@x.com", subject="hello", body="body text",
        )
        assert r["executed"] is False
        assert r["kind"] == "dormant_integration"
        assert r["integration"] == "gmail"

    def test_resend_failure_propagates_executed_false(
        self, monkeypatch, tmp_path
    ):
        """A 4xx from Resend bubbles up as executed=False with the
        Resend-side error message — the LLM can decide whether to
        retry, change the from-address, or escalate."""
        monkeypatch.setenv("GMAIL_TOKEN", str(tmp_path / "no-gmail.json"))
        monkeypatch.setenv("RESEND_API_KEY", "re_bad")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        def fake_post(url, headers=None, json=None, timeout=None):
            class R:
                status_code = 401
                text = '{"message":"Invalid API key"}'
                def json(self_inner):
                    raise ValueError("not used")
            return R()

        with patch("httpx.post", side_effect=fake_post):
            r = _send_email_handler(
                to="dest@x.com", subject="hello", body="body text",
            )

        assert r["executed"] is False
        assert "401" in r["error"]
        assert r["provider"] == "resend"

    def test_header_injection_check_still_runs_before_resend(
        self, monkeypatch, tmp_path
    ):
        """The capability's input validators (header-injection,
        size caps) run BEFORE the Resend fall-through, so they still
        guard the Resend path."""
        monkeypatch.setenv("GMAIL_TOKEN", str(tmp_path / "no-gmail.json"))
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        with pytest.raises(ValueError, match="header injection"):
            _send_email_handler(
                to="dest@x.com\nBcc: attacker@x.com",
                subject="hello", body="body",
            )
