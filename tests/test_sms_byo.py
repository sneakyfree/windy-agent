"""Texting via Windy Text on the owner's own Twilio (WINDY_TEXT_BYO=1).

Windy Text decides consent (GET /sms/recipient) and labels every text; the agent
keeps no local list, adds no footer, uses the approved first-text wording, and
reports a send without the label (prefix_applied) to the owner.
"""
import httpx
import pytest

from windyfly.tools import sms
from windyfly.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WINDY_TEXT_BYO", "1")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-own")
    monkeypatch.setenv("WINDYFLY_AGENT_NAME", "Ava")
    monkeypatch.setenv("WINDY_OWNER_NAME", "Grant Whitmer")
    monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)


def _server(monkeypatch, recipient=None, send_status=200, send_body=None, recipient_status=200):
    calls = {"get": [], "post": []}

    def get(url, params=None, headers=None, timeout=None):
        calls["get"].append({"url": url, "params": params, "auth": headers["Authorization"]})
        return httpx.Response(recipient_status, json=recipient or {}, request=httpx.Request("GET", url))

    def post(url, json=None, headers=None, timeout=None):
        calls["post"].append({"url": url, "json": json, "auth": headers["Authorization"]})
        return httpx.Response(send_status, json=send_body if send_body is not None else {"sid": "SM1", "prefix_applied": True},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(sms.httpx, "get", get)
    monkeypatch.setattr(sms.httpx, "post", post)
    return calls


OWNER_OK = {"approved": True, "opted_out": False, "first_contact": False, "kind": "owner"}
CONTACT_FIRST = {"approved": True, "opted_out": False, "first_contact": True, "kind": "contact"}
CONTACT_LATER = {"approved": True, "opted_out": False, "first_contact": False, "kind": "contact"}


def test_texts_the_owner_with_the_agents_ept_no_from_no_footer(monkeypatch):
    calls = _server(monkeypatch, OWNER_OK)
    out = sms.send_sms(body="Your flight is delayed")
    assert out["status"] == "sent" and out["sent"] is True
    post = calls["post"][0]
    assert post["auth"] == "Bearer ept-own" and post["url"].endswith("/sms/send")
    assert post["json"] == {"body": "Your flight is delayed"}  # no to, no from, no footer
    assert calls["get"][0]["params"] is None  # recipient check for the owner


def test_first_text_to_a_contact_uses_the_approved_wording(monkeypatch):
    calls = _server(monkeypatch, CONTACT_FIRST)
    sms.send_sms(to="+15551230000", body="Dinner moved to 7")
    assert calls["post"][0]["json"]["body"] == (
        "Hi, this is Ava, an AI assistant texting for Grant. Dinner moved to 7. "
        "Reply STOP to opt out, HELP for help.")


def test_later_texts_are_bare(monkeypatch):
    calls = _server(monkeypatch, CONTACT_LATER)
    sms.send_sms(to="+15551230000", body="On my way")
    assert calls["post"][0]["json"]["body"] == "On my way"


def test_not_approved_never_sends(monkeypatch):
    calls = _server(monkeypatch, {"approved": False, "opted_out": False, "first_contact": True, "kind": "contact"})
    out = sms.send_sms(to="+15551230000", body="hi")
    assert out["status"] == "needs_owner_approval" and out["sent"] is False and calls["post"] == []


def test_opted_out_never_sends(monkeypatch):
    calls = _server(monkeypatch, {"approved": True, "opted_out": True, "first_contact": False, "kind": "contact"})
    out = sms.send_sms(to="+15551230000", body="hi")
    assert out["status"] == "opted_out" and calls["post"] == []


def test_consent_check_down_fails_closed(monkeypatch):
    calls = _server(monkeypatch, recipient_status=503)
    out = sms.send_sms(to="+15551230000", body="hi")
    assert out["status"] == "failed" and out["sent"] is False and calls["post"] == []


def test_missing_label_is_reported_to_the_owner(monkeypatch):
    _server(monkeypatch, OWNER_OK, send_body={"sid": "SM1"})  # no prefix_applied
    out = sms.send_sms(body="hi")
    assert out["status"] == "sent_without_label" and "label" in out["notice_to_user"]


@pytest.mark.parametrize("status,code,phrase", [
    (409, "carrier_registration_pending", "carriers haven't approved"),
    (403, "recipient_not_consented", "hasn't agreed"),
    (403, "owner_opted_out", "turned off texts"),
    (425, "quiet_hours", "quiet hours"),
    (429, "spend_cap_reached:daily", "spending limit"),
    (503, "spend_ledger_unavailable", "briefly unavailable"),
])
def test_refusals_become_plain_sentences(monkeypatch, status, code, phrase):
    _server(monkeypatch, OWNER_OK, send_status=status, send_body={"error_code": code})
    out = sms.send_sms(body="hello")
    assert out["status"] == "failed" and out["sent"] is False and phrase in out["error"]


def test_no_confirm_tool_for_the_model_in_byo_mode():
    reg = ToolRegistry()
    sms.register_sms_tools(reg)
    names = {t["function"]["name"] for t in reg.get_schemas()}
    assert "send_sms" in names and "confirm_sms" not in names


def test_off_keeps_the_old_behavior(monkeypatch):
    monkeypatch.delenv("WINDY_TEXT_BYO")
    out = sms.send_sms(to="+15551230000", body="hi")
    assert out["status"] == "unavailable"  # legacy WINDY_PASSPORT_EPT unset: SMS stays off
