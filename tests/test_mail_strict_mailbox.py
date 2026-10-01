"""WINDY_MAIL_STRICT=1: no silent Resend; a missing mailbox says so. Default unchanged."""
import pytest

from windyfly.tools import mail


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(mail, "_adapter", lambda: None)  # no mailbox credential
    monkeypatch.setenv("RESEND_API_KEY", "x")
    monkeypatch.setenv("RESEND_FROM_ADDRESS", "a@example.com")


def test_default_still_falls_to_resend(monkeypatch):
    monkeypatch.delenv("WINDY_MAIL_STRICT", raising=False)
    sent = []
    monkeypatch.setattr(mail, "_resend_send", lambda to, s, b: sent.append(to) or {"status": "sent"})
    out = mail.send_email("bob@example.com", "hi", "body")
    assert out["status"] == "sent" and sent == ["bob@example.com"]


def test_strict_never_uses_resend(monkeypatch):
    monkeypatch.setenv("WINDY_MAIL_STRICT", "1")
    called = []
    monkeypatch.setattr(mail, "_resend_send", lambda *a: called.append(1) or {"status": "sent"})
    out = mail.send_email("bob@example.com", "hi", "body")
    assert out["status"] == "unavailable" and "No mailbox credential" in out["error"]
    assert not called


def test_strict_with_mailbox_still_sends(monkeypatch):
    monkeypatch.setenv("WINDY_MAIL_STRICT", "1")

    class A:
        def send_email(self, to, subject, body):
            return {"status": "sent"}

    monkeypatch.setattr(mail, "_adapter", lambda: A())
    out = mail.send_email("bob@example.com", "hi", "body")
    assert out["status"] == "sent" and out["provider"] == "windymail"


def test_gmail_capability_not_registered_for_resend_only_when_strict(monkeypatch):
    from windyfly.agent.capabilities import email as cap

    class Reg:
        def __init__(self):
            self.items = []

        def register(self, c):
            self.items.append(c)

    monkeypatch.setattr(cap, "_is_configured", lambda: False)
    monkeypatch.delenv("WINDY_MAIL_STRICT", raising=False)
    r = Reg()
    cap.register_email_capabilities(r)
    assert r.items  # today's behaviour: Resend alone registers email.send
    monkeypatch.setenv("WINDY_MAIL_STRICT", "1")
    r = Reg()
    cap.register_email_capabilities(r)
    assert not r.items


def test_strict_explicit_resend_opt_in_is_visible(monkeypatch):
    monkeypatch.setenv("WINDY_MAIL_STRICT", "1")
    monkeypatch.setenv("WINDY_MAIL_ALLOW_RESEND", "1")
    monkeypatch.setattr(mail, "_resend_send", lambda to, s, b: {"status": "sent"})
    out = mail.send_email("bob@example.com", "hi", "body")
    assert out["status"] == "sent" and out["provider"] == "resend"
    assert "not from your agent's own mailbox" in out["notice"]
