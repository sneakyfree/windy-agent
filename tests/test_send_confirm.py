"""Owner confirmation before outbound sends (dark: WINDY_SEND_CONFIRM=1)."""
import asyncio

import pytest

from windyfly.agent.capabilities import Band
from windyfly.channels import base, identity
from windyfly.tools import mail

OWNER = "@owner:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    mail._PENDING.clear()
    sent = []

    def fake_now(to, subject, body, *, cc=None, bcc=None, approved_by=None):
        sent.append({"to": to, "subject": subject, "body": body, "approved_by": approved_by})
        return {"status": "sent", "provider": "windymail"}

    monkeypatch.setattr(mail, "_send_email_now", fake_now)
    monkeypatch.setattr(identity, "resolve_band",
                        lambda platform, sender, **kw: Band.OWNER if sender == OWNER else Band.SANDBOX)
    yield sent
    mail._PENDING.clear()


def _incoming(text, sender=OWNER):
    return asyncio.run(base.handle_incoming(text, {"platform": "matrix", "sender_id": sender}))


def test_off_by_default_sends_immediately(monkeypatch, _clean):
    monkeypatch.delenv("WINDY_SEND_CONFIRM", raising=False)
    out = mail.send_email("a@b.com", "hi", "body")
    assert out["status"] == "sent" and len(_clean) == 1 and not mail.pending_drafts()


def test_on_only_drafts_and_says_not_sent(monkeypatch, _clean):
    monkeypatch.setenv("WINDY_SEND_CONFIRM", "1")
    out = mail.send_email("a@b.com", "hi", "body")
    assert out["status"] == "pending_owner_approval" and "NOT SENT" in out["note"]
    assert _clean == []
    assert len(mail.pending_drafts()) == 1


def test_owner_send_approves_and_names_the_approver(monkeypatch, _clean):
    monkeypatch.setenv("WINDY_SEND_CONFIRM", "1")
    mail.send_email("a@b.com", "hi", "body")
    was_cmd, reply = _incoming("Send.")
    assert was_cmd and "Sent to a@b.com" in reply
    assert _clean[0]["approved_by"] == OWNER and _clean[0]["to"] == "a@b.com"
    assert not mail.pending_drafts()


def test_stranger_cannot_approve(monkeypatch, _clean):
    monkeypatch.setenv("WINDY_SEND_CONFIRM", "1")
    mail.send_email("a@b.com", "hi", "body")
    was_cmd, _ = _incoming("send", sender="@stranger:chat.windychat.ai")
    assert not was_cmd and _clean == [] and mail.pending_drafts()


def test_cancel_drops_drafts(monkeypatch, _clean):
    monkeypatch.setenv("WINDY_SEND_CONFIRM", "1")
    mail.send_email("a@b.com", "hi", "body")
    was_cmd, reply = _incoming("cancel")
    assert was_cmd and "Nothing was sent" in reply and _clean == [] and not mail.pending_drafts()


def test_send_word_with_no_draft_is_just_chat(monkeypatch, _clean):
    monkeypatch.setenv("WINDY_SEND_CONFIRM", "1")
    was_cmd, _ = _incoming("send")
    assert not was_cmd


def test_flag_off_never_intercepts(monkeypatch, _clean):
    monkeypatch.delenv("WINDY_SEND_CONFIRM", raising=False)
    mail._PENDING["x"] = {"to": "a@b.com", "subject": "s", "body": "b", "at": 9e18}
    was_cmd, _ = _incoming("send")
    assert not was_cmd and _clean == []


def test_the_model_has_no_approve_tool():
    from windyfly.tools.registry import ToolRegistry

    reg = ToolRegistry()
    mail.register_mail_tools(reg)
    names = {t["function"]["name"] for t in reg.get_schemas()}
    assert {"send_email", "list_inbox"} <= names  # not vacuous: the tools did register
    assert not any("approve" in n or "confirm" in n for n in names)
