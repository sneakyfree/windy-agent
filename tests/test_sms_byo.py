"""Texting via Windy Text on the owner's own Twilio (dark: WINDY_TEXT_BYO=1)."""
import asyncio
import json

import httpx
import pytest

from windyfly.agent.capabilities import Band
from windyfly.channels import base, identity
from windyfly.tools import sms
from windyfly.tools.registry import ToolRegistry

OWNER = "@owner:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WINDY_TEXT_BYO", "1")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-own")
    monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
    sms._pending.clear()
    sms._approved_mem.clear()
    monkeypatch.setattr(sms, "_db", None, raising=False)
    monkeypatch.setattr(identity, "resolve_band",
                        lambda platform, sender, **kw: Band.OWNER if sender == OWNER else Band.SANDBOX)
    yield
    sms._pending.clear()


def _capture(monkeypatch, status=200, payload=None):
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "auth": headers["Authorization"], "json": json})
        return httpx.Response(status, json=payload or {"sid": "SM1"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(sms.httpx, "post", post)
    return calls


def test_uses_the_agents_own_ept_and_never_a_from(monkeypatch):
    calls = _capture(monkeypatch)
    out = sms.send_sms(body="hello")  # no `to` = the owner
    assert out["status"] == "sent"
    assert calls[0]["auth"] == "Bearer ept-own"
    assert "from" not in calls[0]["json"] and "to" not in calls[0]["json"]
    assert calls[0]["url"].endswith("/sms/send")


def test_footer_kept_even_though_server_prefixes(monkeypatch):
    calls = _capture(monkeypatch)
    sms.send_sms(body="hello")
    assert "STOP" in calls[0]["json"]["body"]


@pytest.mark.parametrize("status,code,phrase", [
    (409, "carrier_registration_pending", "carriers haven't approved"),
    (403, "recipient_not_consented", "hasn't agreed"),
    (403, "owner_opted_out", "turned off texts"),
    (425, "quiet_hours", "quiet hours"),
    (429, "spend_cap_reached:daily", "spending limit"),
    (503, "spend_ledger_unavailable", "briefly unavailable"),
])
def test_refusals_become_plain_sentences(monkeypatch, status, code, phrase):
    _capture(monkeypatch, status, {"error_code": code})
    out = sms.send_sms(body="hello")
    assert out["status"] == "failed" and out["sent"] is False and phrase in out["error"]


def test_first_contact_is_approved_by_the_owners_yes_not_the_model(monkeypatch):
    calls = _capture(monkeypatch)
    out = sms.send_sms(to="+15551230000", body="hi")
    assert out["status"] == "confirm_required" and calls == []
    reg = ToolRegistry()
    sms.register_sms_tools(reg)
    names = {t["function"]["name"] for t in reg.get_schemas()}
    assert "send_sms" in names and "confirm_sms" not in names
    was, reply = asyncio.run(base.handle_incoming("yes", {"platform": "matrix", "sender_id": OWNER}))
    assert was and "Texted +15551230000" in reply and len(calls) == 1


def test_a_stranger_saying_yes_approves_nothing(monkeypatch):
    calls = _capture(monkeypatch)
    sms.send_sms(to="+15551230000", body="hi")
    was, _ = asyncio.run(base.handle_incoming("yes", {"platform": "matrix", "sender_id": "@x:y"}))
    assert not was and calls == [] and sms._pending


def test_off_keeps_the_old_behavior(monkeypatch):
    monkeypatch.delenv("WINDY_TEXT_BYO")
    out = sms.send_sms(to="+15551230000", body="hi")
    assert out["status"] == "unavailable"  # legacy WINDY_PASSPORT_EPT unset: SMS stays off
    reg = ToolRegistry()
    sms.register_sms_tools(reg)
    assert "confirm_sms" in {t["function"]["name"] for t in reg.get_schemas()}
    assert json.dumps(out)  # serialisable
