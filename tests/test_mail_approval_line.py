"""An approved send says so without naming the approver (Hub 10-12).

The approval line went out as "(Sent with the approval of @owner:server.)": every outside recipient
learned the owner's Windy Chat handle and homeserver. The mail now says "(Sent with the owner's
approval.)" only, and the approver stays in our own log as an id plus counts, never the mail's text.
Synthetic addresses and a fake adapter only: nothing is sent.
"""

from __future__ import annotations

import logging

import pytest

from windyfly.tools import mail

APPROVER = "@probe-owner:chat.example.invalid"
TO = "probe-recipient@example.invalid"
LINE = "(Sent with the owner's approval.)"


class FakeAdapter:
    def __init__(self) -> None:
        self.bodies: list[str] = []

    def send_email(self, to, subject, body, cc=None, bcc=None):
        self.bodies.append(body)
        return {"status": "sent", "message_id": "m1"}


@pytest.fixture
def adapter(monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(mail, "_adapter", lambda: fake)
    return fake


def test_an_approved_send_says_so_without_the_approvers_handle(adapter):
    mail._send_email_now(TO, "hi", "Hello there.", approved_by=APPROVER)
    body = adapter.bodies[0]
    assert LINE in body
    assert APPROVER not in body and "probe-owner" not in body
    assert "chat.example.invalid" not in body
    assert "@" not in body.replace(TO, "")


def test_the_approval_line_is_only_on_an_approved_send(adapter):
    mail._send_email_now(TO, "hi", "Hello there.")
    assert "approval" not in adapter.bodies[0]


def test_the_approver_is_logged_as_an_id_and_counts_never_the_mail(adapter, caplog):
    with caplog.at_level(logging.INFO, logger="windyfly.tools.mail"):
        mail._send_email_now([TO, "second@example.invalid"], "Private subject", "Private words.",
                             approved_by=APPROVER)
    logged = "\n".join(r.getMessage() for r in caplog.records if r.name == "windyfly.tools.mail")
    assert APPROVER in logged and "2 recipient" in logged
    assert "Private subject" not in logged and "Private words" not in logged and TO not in logged
