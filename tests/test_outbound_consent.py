"""Outbound consent to third parties (legal review, 2026-09-23).

SMS: the first text to a number needs the owner's yes (confirm_required →
confirm_sms), every text carries an AI sign-off with STOP, and a
recipient_opted_out answer is final. SMS itself stays off until a sender
enforces STOP. Email: every message says it's from an AI agent acting for
the owner, and the Resend path carries X-Windy-Agent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from windyfly.memory.database import Database
from windyfly.tools import mail as mail_mod
from windyfly.tools import outbound_identity
from windyfly.tools import sms as sms_mod
from windyfly.tools.registry import ToolRegistry

NUMBER = "+15557654321"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WINDY_TEXT_BASE_URL", "https://api.windytext.test")
    monkeypatch.setenv("WINDY_PASSPORT_EPT", "ept")
    monkeypatch.setenv("WINDYFLY_AGENT_NAME", "Pip")
    monkeypatch.setenv("WINDY_OWNER_NAME", "Grant Whitmer")
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-0001")
    monkeypatch.setattr(sms_mod, "_approved_mem", set())
    monkeypatch.setattr(sms_mod, "_pending", {})
    outbound_identity.set_config({})
    db = Database(":memory:")
    monkeypatch.setattr(sms_mod, "_db", db)
    yield db


def _ok(sid="SM-1"):
    r = MagicMock(status_code=201)
    r.json.return_value = {"sid": sid, "to": NUMBER, "from": "+1000"}
    return r


# ── SMS: the first-contact gate ─────────────────────────────────────


@patch("windyfly.tools.sms.httpx.post")
def test_first_text_needs_the_owners_yes_then_later_texts_flow(post, _env):
    post.return_value = _ok()
    first = sms_mod.send_sms(NUMBER, "Dinner at 7?")
    assert first["status"] == "confirm_required"
    assert first["question"] == f"Text {NUMBER} for the first time? Reply yes to allow."
    post.assert_not_called()

    sent = sms_mod.confirm_sms(first["confirm_token"])
    assert sent["status"] == "sent"
    assert post.call_args.kwargs["json"]["body"].startswith("Dinner at 7?\n")
    row = _env.fetchone("SELECT approved_by FROM sms_approved WHERE number = ?", (NUMBER,))
    assert row["approved_by"] == "owner"

    again = sms_mod.send_sms(NUMBER, "Running late")
    assert again["status"] == "sent" and post.call_count == 2


@patch("windyfly.tools.sms.httpx.post")
def test_tokens_are_single_use_and_unknown_ones_refused(post, _env):
    post.return_value = _ok()
    token = sms_mod.send_sms(NUMBER, "hi")["confirm_token"]
    assert sms_mod.confirm_sms("not-a-token")["status"] == "refused"
    assert sms_mod.confirm_sms(token)["status"] == "sent"
    assert sms_mod.confirm_sms(token)["status"] == "refused"
    assert post.call_count == 1


@patch("windyfly.tools.sms.httpx.post")
def test_expired_token_is_refused_and_nothing_is_approved(post, _env, monkeypatch):
    token = sms_mod.send_sms(NUMBER, "hi")["confirm_token"]
    real = sms_mod.time.time
    monkeypatch.setattr(sms_mod.time, "time", lambda: real() + 601)
    assert sms_mod.confirm_sms(token)["status"] == "refused"
    post.assert_not_called()
    assert _env.fetchone("SELECT 1 FROM sms_approved WHERE number = ?", (NUMBER,)) is None


@patch("windyfly.tools.sms.httpx.post")
def test_the_yes_sends_exactly_the_approved_text(post, _env):
    post.return_value = _ok()
    t1 = sms_mod.send_sms(NUMBER, "first draft")["confirm_token"]
    sms_mod.send_sms(NUMBER, "second draft")
    sms_mod.confirm_sms(t1)
    assert post.call_args.kwargs["json"]["body"].startswith("first draft\n")


# ── SMS: sign-off, STOP, availability ───────────────────────────────


@patch("windyfly.tools.sms.httpx.post")
def test_every_text_carries_the_ai_sign_off(post, _env):
    post.return_value = _ok()
    sms_mod._approve(NUMBER)
    sms_mod.send_sms(NUMBER, "hello")
    assert post.call_args.kwargs["json"]["body"] == (
        "hello\n— Pip, AI assistant for Grant. Reply STOP to opt out."
    )


@patch("windyfly.tools.sms.httpx.post")
def test_sign_off_without_a_known_owner(post, _env, monkeypatch):
    monkeypatch.delenv("WINDY_OWNER_NAME")
    post.return_value = _ok()
    sms_mod._approve(NUMBER)
    sms_mod.send_sms(NUMBER, "hello")
    assert post.call_args.kwargs["json"]["body"].endswith("— Pip, AI assistant. Reply STOP to opt out.")


@patch("windyfly.tools.sms.httpx.post")
def test_opted_out_is_final_and_never_retried(post, _env):
    r = MagicMock(status_code=409)
    r.json.return_value = {"error_code": "recipient_opted_out", "detail": "opted out"}
    post.return_value = r
    sms_mod._approve(NUMBER)
    out = sms_mod.send_sms(NUMBER, "hello")
    assert out["status"] == "opted_out" and "STOP" in out["error"]
    assert post.call_count == 1


def test_sms_is_honestly_unavailable_without_a_sender(monkeypatch):
    monkeypatch.delenv("WINDY_PASSPORT_EPT")
    # The agent's normal passport token must NOT turn SMS on (no STOP enforcement yet).
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "a-real-ept")
    out = sms_mod.send_sms(NUMBER, "hello")
    assert out == {"status": "unavailable", "error": "Texting isn't available yet; I can reach them by email or you can message them in Windy Chat."}


def test_too_long_with_the_sign_off_is_refused(_env):
    sms_mod._approve(NUMBER)
    out = sms_mod.send_sms(NUMBER, "x" * 1590)
    assert out["status"] == "failed" and "too long" in out["error"]


def test_confirm_sms_is_registered_and_the_description_demands_relay():
    reg = ToolRegistry()
    sms_mod.register_sms_tools(reg)
    fns = {s["function"]["name"]: s["function"] for s in reg.get_schemas()}
    assert "confirm_sms" in fns
    assert "RELAY THE QUESTION VERBATIM" in fns["send_sms"]["description"]


def test_migration_13_creates_the_approvals_table():
    db = Database(":memory:")
    assert db.fetchone("SELECT name FROM sqlite_master WHERE name = 'sms_approved'") is not None


# ── Email: AI footer + header ───────────────────────────────────────


def test_windy_mail_path_gets_the_footer():
    adapter = MagicMock()
    adapter.send_email.return_value = {"status": "sent"}
    with patch.object(mail_mod, "_adapter", return_value=adapter):
        mail_mod.send_email("a@example.com, b@example.com", "Hi", "Body")
    for call in adapter.send_email.call_args_list:
        body = call.args[2]
        assert body.endswith("Sent by Pip, an AI agent acting for Grant Whitmer.\n")
        assert body.count("Sent by Pip") == 1


def test_resend_path_gets_the_footer_and_the_agent_header(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_FROM_ADDRESS", "pip@windyfly.ai")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(json)
        r = MagicMock(status_code=200)
        r.json.return_value = {"id": "rs-1"}
        return r

    with patch.object(mail_mod, "_adapter", return_value=None), patch("httpx.post", side_effect=fake_post):
        assert mail_mod.send_email("a@example.com", "Hi", "Body")["status"] == "sent"
    assert seen["text"].endswith("Sent by Pip, an AI agent acting for Grant Whitmer.\n")
    assert seen["headers"] == {"X-Windy-Agent": "ET26-TEST-0001"}


def test_email_footer_without_a_known_owner(monkeypatch):
    monkeypatch.delenv("WINDY_OWNER_NAME")
    assert mail_mod.with_ai_footer("x").endswith("Sent by Pip, an AI agent acting for its owner.\n")


def test_agent_name_comes_from_config_first(monkeypatch):
    outbound_identity.set_config({"agent": {"name": "Biscuit"}})
    try:
        assert outbound_identity.email_footer().startswith("Sent by Biscuit,")
    finally:
        outbound_identity.set_config({})
