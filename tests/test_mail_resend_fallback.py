"""Mail-tool Resend fallback tests.

Pin the contract: ``send_email`` falls through to Resend's HTTP API
when WindyMail isn't configured (no JMAP token) but Resend is
(``RESEND_API_KEY`` + ``RESEND_FROM_ADDRESS``).

Motivation: Stalwart 0.16's Bearer-auth rejection (see ACCESS_LOCKBOX
"Tonight tested it live and traced the failure", 2026-05-05) plus
the deployed-server-only WINDYMAIL_PROVISION_SERVICE_TOKEN means
fresh Windy instances can't easily get a JMAP mailbox. Resend's
verified-domain pool works out of the box with one env pair and
sends from `bot@<verified-domain>` cleanly.

Tests cover:

  1. WindyMail is preferred when both paths are configured
  2. Resend takes over when only Resend is configured
  3. "unavailable" structured error when neither path is configured
  4. Resend payload shape (auth header, JSON body) is correct
  5. Resend 2xx → status=sent + message_id + provider="resend"
  6. Resend non-2xx → status=failed + error includes HTTP code
  7. Resend timeout / transport error surfaces clearly
  8. Multi-recipient send via Resend annotates each entry with provider
  9. RESEND_API_KEY without RESEND_FROM_ADDRESS does NOT activate path
     (prevents misconfigured-half-fallback from doing surprising sends)
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from windyfly.tools.mail import (
    _resend_configured,
    _resend_send,
    send_email,
)


# ─── Configuration gating ────────────────────────────────────────


class TestResendConfigured:

    def test_both_set(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")
        assert _resend_configured() is True

    def test_key_only(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.delenv("RESEND_FROM_ADDRESS", raising=False)
        assert _resend_configured() is False, \
            "missing FROM_ADDRESS should NOT activate Resend"

    def test_from_only(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")
        assert _resend_configured() is False

    def test_neither(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.delenv("RESEND_FROM_ADDRESS", raising=False)
        assert _resend_configured() is False


# ─── Path selection in send_email ────────────────────────────────


class TestPathSelection:
    """When BOTH WindyMail and Resend are configured, WindyMail wins
    (it owns the trust-gate + rate-limiter plumbing). Resend is a
    strict fallback for the WindyMail-unavailable case."""

    def test_windymail_wins_when_both_configured(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        class FakeAdapter:
            def send_email(self, to, subject, body):
                return {"status": "sent", "message_id": "wm-1"}

        with patch("windyfly.tools.mail._adapter", return_value=FakeAdapter()):
            r = send_email("dest@x.com", "hi", "body")

        assert r["status"] == "sent"
        assert r["message_id"] == "wm-1"
        assert r["provider"] == "windymail"

    def test_resend_used_when_windymail_unavailable(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        def fake_post(url, headers=None, json=None, timeout=None):
            class R:
                status_code = 200
                text = '{"id": "rs-1"}'
                def json(self_inner):
                    return {"id": "rs-1"}
            return R()

        with patch("windyfly.tools.mail._adapter", return_value=None), \
             patch("httpx.post", side_effect=fake_post):
            r = send_email("dest@x.com", "hi", "body")

        assert r["status"] == "sent"
        assert r["provider"] == "resend"
        assert r["message_id"] == "rs-1"

    def test_unavailable_when_neither_path_configured(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.delenv("RESEND_FROM_ADDRESS", raising=False)
        with patch("windyfly.tools.mail._adapter", return_value=None):
            r = send_email("dest@x.com", "hi", "body")
        assert r["status"] == "unavailable"
        # Error message must mention BOTH paths so the LLM can route
        # the user to the simpler one (Resend) when JMAP is too much work.
        assert "WINDYMAIL" in r["error"]
        assert "RESEND" in r["error"]


# ─── Resend payload shape ────────────────────────────────────────


class TestResendPayload:

    def test_auth_header_and_body(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test_abc")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["timeout"] = timeout
            class R:
                status_code = 200
                text = "{}"
                def json(self_inner):
                    return {"id": "x"}
            return R()

        with patch("httpx.post", side_effect=fake_post):
            _resend_send("dest@x.com", "subject line", "body text")

        assert captured["url"] == "https://api.resend.com/emails"
        assert captured["headers"]["Authorization"] == "Bearer re_test_abc"
        assert captured["headers"]["Content-Type"] == "application/json"
        assert captured["json"]["from"] == "windy@windyfly.ai"
        assert captured["json"]["to"] == ["dest@x.com"]
        assert captured["json"]["subject"] == "subject line"
        assert captured["json"]["text"] == "body text"
        assert captured["timeout"] == 15


# ─── Resend response handling ────────────────────────────────────


class TestResendResponses:

    def test_202_accepted(self, monkeypatch):
        # Resend uses 200/201/202 for success — all should map to sent
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        for code in (200, 201, 202):
            def fake_post(url, headers=None, json=None, timeout=None, _code=code):
                class R:
                    status_code = _code
                    text = '{"id":"abc"}'
                    def json(self_inner):
                        return {"id": "abc"}
                return R()
            with patch("httpx.post", side_effect=fake_post):
                r = _resend_send("d@x.com", "s", "b")
            assert r["status"] == "sent", f"code {code} should map to sent"
            assert r["message_id"] == "abc"

    def test_non_2xx_returns_failed(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_bad")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        def fake_post(url, headers=None, json=None, timeout=None):
            class R:
                status_code = 401
                text = '{"message":"API key is invalid"}'
                def json(self_inner):
                    raise ValueError("not used in this path")
            return R()

        with patch("httpx.post", side_effect=fake_post):
            r = _resend_send("d@x.com", "s", "b")
        assert r["status"] == "failed"
        assert "401" in r["error"]
        assert "API key is invalid" in r["error"]
        assert r["provider"] == "resend"

    def test_timeout_returns_clear_error(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        def fake_post(*a, **kw):
            raise httpx.TimeoutException("timed out")

        with patch("httpx.post", side_effect=fake_post):
            r = _resend_send("d@x.com", "s", "b")
        assert r["status"] == "failed"
        assert "timed out" in r["error"].lower()
        assert r["provider"] == "resend"

    def test_transport_error_returns_clear_error(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        def fake_post(*a, **kw):
            raise httpx.ConnectError("could not connect")

        with patch("httpx.post", side_effect=fake_post):
            r = _resend_send("d@x.com", "s", "b")
        assert r["status"] == "failed"
        assert "transport" in r["error"].lower() or "connect" in r["error"].lower()


# ─── Multi-recipient through Resend ──────────────────────────────


class TestMultiRecipientResend:

    def test_each_entry_annotated_with_provider(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("RESEND_FROM_ADDRESS", "windy@windyfly.ai")

        # Alternate success/fail per call
        calls = [0]

        def fake_post(url, headers=None, json=None, timeout=None):
            i = calls[0]
            calls[0] += 1
            class R:
                status_code = 200 if i == 0 else 422
                text = '{"id":"ok"}' if i == 0 else '{"message":"bad addr"}'
                def json(self_inner):
                    return {"id": "ok"} if i == 0 else {"message": "bad addr"}
            return R()

        with patch("windyfly.tools.mail._adapter", return_value=None), \
             patch("httpx.post", side_effect=fake_post):
            r = send_email("ok@x.com, bad@x.com", "s", "b")

        assert r["status"] == "partial"
        assert r["successes"] == 1
        assert r["total"] == 2
        assert all(p.get("provider") == "resend" for p in r["per_recipient"])
