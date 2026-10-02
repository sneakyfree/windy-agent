"""One send path (Hub, 2026-10-02): an agent sends ONLY from its own Windy Mail mailbox.

A mail went out "From: office@windyword.ai" outside Windy Mail. The Resend, Gmail and
SendGrid senders are gone; From and Reply-To are always the agent's own address.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from windyfly.tools import mail as mail_mod
from windyfly.tools.mail import send_email


@pytest.mark.parametrize("env", [
    {"RESEND_API_KEY": "re_test", "RESEND_FROM_ADDRESS": "office@windyword.ai"},
    {"RESEND_API_KEY": "re_test", "RESEND_FROM_ADDRESS": "x@y.z", "WINDY_MAIL_ALLOW_RESEND": "1"},
    {"SENDGRID_API_KEY": "SG.test", "WINDYFLY_EMAIL_ADDRESS": "fly@windyfly.ai"},
    {},
])
def test_no_mailbox_never_sends_anywhere(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    posted = []
    with patch.object(mail_mod, "_adapter", return_value=None), \
            patch("httpx.post", side_effect=lambda *a, **k: posted.append(a)), \
            patch("urllib.request.urlopen", side_effect=lambda *a, **k: posted.append(a)):
        out = send_email("dest@x.com", "hi", "body")
    assert out["status"] == "unavailable"
    assert "nothing was sent" in out["error"]
    assert posted == []


def test_no_fallback_helpers_left():
    for name in ("_resend_send", "_resend_configured", "strict_mailbox"):
        assert not hasattr(mail_mod, name), name


def test_windy_mail_sends_and_is_named(monkeypatch):
    class FakeAdapter:
        def send_email(self, to, subject, body):
            return {"status": "sent", "message_id": "wm-1"}

    with patch.object(mail_mod, "_adapter", return_value=FakeAdapter()):
        r = send_email("dest@x.com", "hi", "body")
    assert r["status"] == "sent" and r["message_id"] == "wm-1" and r["provider"] == "windymail"


def test_multi_recipient_entries_are_annotated(monkeypatch):
    results = iter([{"status": "sent", "message_id": "a"}, {"status": "failed", "error": "bad addr"}])

    class FakeAdapter:
        def send_email(self, to, subject, body):
            return next(results)

    with patch.object(mail_mod, "_adapter", return_value=FakeAdapter()):
        r = send_email("ok@x.com, bad@x.com", "s", "b")
    assert (r["status"], r["successes"], r["total"]) == ("partial", 1, 2)
    assert all(p.get("provider") == "windymail" for p in r["per_recipient"])


def test_bridge_email_send_uses_windy_mail(monkeypatch):
    """The local bridge's email.send used SendGrid with its own From; now the one path."""
    import asyncio

    from windyfly.bridge import uds_server

    seen = {}
    monkeypatch.setattr(mail_mod, "_send_email_now",
                        lambda to, subject, body, **kw: seen.update(to=to) or {"status": "sent"})
    out = asyncio.run(uds_server.UDSBridge._handle_email_send(
        object.__new__(uds_server.UDSBridge), {"to": "a@b.com", "subject": "s", "body": "b"}))
    assert out == {"status": "sent"} and seen["to"] == "a@b.com"
