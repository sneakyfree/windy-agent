"""Texting via Windy Text on the owner's own Twilio (WINDY_TEXT_BYO=1).

Windy Text decides consent (GET /sms/recipient) and labels every text; the agent
keeps no local list, adds no footer, uses the approved first-text wording, and
reports a send without the label (prefix_applied) to the owner. Every request is mode B only
(EPT+agent for windy-telephony + a DPoP proof for that request); the legacy passport token is never sent.
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
    from windyfly.eternitas import agent_keys as ak

    monkeypatch.setattr(ak, "request_agent_token", lambda aud: {"token": f"tok-{aud}"})
    monkeypatch.setattr(ak, "service_dpop", lambda m, u: f"proof-{m}-{u}")


def _server(monkeypatch, recipient=None, send_status=200, send_body=None, recipient_status=200):
    calls = {"get": [], "post": []}

    def get(url, params=None, headers=None, timeout=None):
        calls["get"].append({"url": url, "params": params, "auth": headers["Authorization"],
                             "dpop": headers.get("DPoP")})
        return httpx.Response(recipient_status, json=recipient or {}, request=httpx.Request("GET", url))

    def post(url, json=None, headers=None, timeout=None):
        calls["post"].append({"url": url, "json": json, "auth": headers["Authorization"],
                              "dpop": headers.get("DPoP")})
        return httpx.Response(send_status, json=send_body if send_body is not None else {"sid": "SM1", "prefix_applied": True},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(sms.httpx, "get", get)
    monkeypatch.setattr(sms.httpx, "post", post)
    return calls


OWNER_OK = {"approved": True, "opted_out": False, "first_contact": False, "kind": "owner"}
CONTACT_FIRST = {"approved": True, "opted_out": False, "first_contact": True, "kind": "contact"}
CONTACT_LATER = {"approved": True, "opted_out": False, "first_contact": False, "kind": "contact"}


def test_texts_the_owner_in_mode_b_no_from_no_footer(monkeypatch):
    calls = _server(monkeypatch, OWNER_OK)
    out = sms.send_sms(body="Your flight is delayed")
    assert out["status"] == "sent" and out["sent"] is True
    post = calls["post"][0]
    assert post["auth"] == "Bearer tok-windy-telephony" and post["url"].endswith("/sms/send")
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


def test_not_approved_files_one_request_and_never_sends(monkeypatch):
    calls = _server(monkeypatch, {"approved": False, "opted_out": False, "first_contact": True, "kind": "contact"},
                    send_status=202, send_body={"status": "pending"})
    out = sms.send_sms(to="+15551230000", body="hi")
    assert out["status"] == "needs_owner_approval" and out["requested"] and out["sent"] is False
    assert [c["url"].rsplit("/", 2)[-2:] for c in calls["post"]] == [["recipient", "request"]]
    assert calls["post"][0]["json"] == {"to": "+15551230000"}
    assert "asked for your OK in Windy" in out["error"]


def test_request_says_opted_out(monkeypatch):
    _server(monkeypatch, {"approved": False, "opted_out": False, "first_contact": True, "kind": "contact"},
            send_status=403, send_body={"detail": "recipient_opted_out"})
    assert sms.send_sms(to="+15551230000", body="hi")["status"] == "opted_out"


def test_too_many_pending(monkeypatch):
    _server(monkeypatch, {"approved": False, "opted_out": False, "first_contact": True, "kind": "contact"},
            send_status=429, send_body={"detail": "too_many_pending_requests"})
    assert "waiting for your OK" in sms.send_sms(to="+15551230000", body="hi")["error"]


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


def test_code_in_detail_also_becomes_a_plain_sentence(monkeypatch):
    """Live 10-01: Windy Text's 409 carried the code in `detail`, and the raw code reached the user."""
    _server(monkeypatch, OWNER_OK, send_status=409, send_body={"detail": "carrier_registration_pending"})
    out = sms.send_sms(body="hello")
    assert out["error_code"] == "carrier_registration_pending"
    assert "carriers haven't approved" in out["error"]


def test_windy_texts_own_wording_is_never_passed_on(monkeypatch):
    _server(monkeypatch, OWNER_OK, send_status=400, send_body={"detail": "Body is empty."})
    out = sms.send_sms(body="hello")
    assert "Body is empty" not in out["error"] and "answered 400" in out["error"] and out["error_code"] is None


@pytest.mark.parametrize("status,detail,phrase", [
    (401, "EPT invalid: dpop_replay", "couldn't sign in to Windy Text"),
    (403, "passport_not_in_good_standing", "standing doesn't allow"),
    (503, "keys_unavailable", "briefly unavailable"),
    (503, "revocation_unavailable", "briefly unavailable"),
])
def test_auth_refusals_on_send_are_plain_and_never_echo_detail(monkeypatch, status, detail, phrase):
    _server(monkeypatch, OWNER_OK, send_status=status, send_body={"detail": detail})
    out = sms.send_sms(body="hello")
    assert out["status"] == "failed" and phrase in out["error"] and detail not in out["error"]


@pytest.mark.parametrize("status,detail,phrase", [
    (401, "EPT invalid: expired", "couldn't sign in to Windy Text"),
    (403, "passport_not_in_good_standing", "standing doesn't allow"),
    (503, "keys_unavailable", "briefly unavailable"),
])
def test_auth_refusals_on_the_approval_request_are_plain(monkeypatch, status, detail, phrase):
    _server(monkeypatch, {"approved": False, "opted_out": False, "first_contact": True, "kind": "contact"},
            send_status=status, send_body={"detail": detail})
    out = sms.send_sms(to="+15551230000", body="hi")
    assert out["status"] == "failed" and phrase in out["error"] and detail not in out["error"]


@pytest.mark.parametrize("status,phrase", [(401, "couldn't sign in"), (403, "standing doesn't allow")])
def test_consent_check_refusing_the_credential_says_so(monkeypatch, status, phrase):
    calls = _server(monkeypatch, recipient_status=status)
    out = sms.send_sms(to="+15551230000", body="hi")
    assert phrase in out["error"] and "couldn't check" not in out["error"] and calls["post"] == []


def test_no_confirm_tool_for_the_model_in_byo_mode():
    reg = ToolRegistry()
    sms.register_sms_tools(reg)
    names = {t["function"]["name"] for t in reg.get_schemas()}
    assert "send_sms" in names and "confirm_sms" not in names


def test_off_keeps_the_old_behavior(monkeypatch):
    monkeypatch.delenv("WINDY_TEXT_BYO")
    out = sms.send_sms(to="+15551230000", body="hi")
    assert out["status"] == "unavailable"  # legacy WINDY_PASSPORT_EPT unset: SMS stays off


def test_every_request_is_mode_b_with_a_proof_for_its_own_method_and_url(monkeypatch):
    calls = _server(monkeypatch, {"approved": False, "opted_out": False, "first_contact": True, "kind": "contact"},
                    send_status=202, send_body={"status": "pending"})
    sms.send_sms(to="+15551230000", body="hi")
    get, req = calls["get"][0], calls["post"][0]
    assert get["auth"] == req["auth"] == "Bearer tok-windy-telephony"
    assert get["dpop"] == "proof-GET-https://api.windytext.com/sms/recipient"  # no query in htu
    assert req["dpop"] == "proof-POST-https://api.windytext.com/sms/recipient/request"

    calls = _server(monkeypatch, OWNER_OK)
    sms.send_sms(body="hi")
    assert calls["post"][0]["dpop"] == "proof-POST-https://api.windytext.com/sms/send"


def test_the_legacy_passport_token_is_never_sent(monkeypatch):
    calls = _server(monkeypatch, CONTACT_LATER)
    sms.send_sms(to="+15551230000", body="hi")
    sent = [c["auth"] for c in calls["get"] + calls["post"]]
    assert sent and all("ept-own" not in a for a in sent)


@pytest.mark.parametrize("code", ["no_key", "unknown_audience", "unreachable", "passport_not_born"])
def test_no_agent_token_means_nothing_is_sent(monkeypatch, code):
    from windyfly.eternitas import agent_keys as ak

    calls = _server(monkeypatch, OWNER_OK)

    def refuse(aud):
        raise ak.AgentTokenError(code)

    monkeypatch.setattr(ak, "request_agent_token", refuse)
    out = sms.send_sms(body="hi")
    assert out["status"] == "failed" and out["sent"] is False and out["error_code"] == code
    assert "couldn't sign in to Windy Text" in out["error"]
    assert calls["get"] == [] and calls["post"] == []  # not even the consent read


def test_revoked_passport_is_refused_in_plain_words(monkeypatch):
    from windyfly.eternitas import agent_keys as ak

    calls = _server(monkeypatch, OWNER_OK)

    def revoked(aud):
        raise ak.AgentTokenError("passport_revoked")

    monkeypatch.setattr(ak, "request_agent_token", revoked)
    out = sms.send_sms(body="hi")
    assert out["status"] == "failed" and "standing" in out["error"]
    assert calls["get"] == [] and calls["post"] == []


def test_byo_needs_no_legacy_token(monkeypatch):
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN")
    calls = _server(monkeypatch, OWNER_OK)
    assert sms.send_sms(body="hi")["status"] == "sent" and len(calls["post"]) == 1


def test_owner_asked_in_inbox_wording(monkeypatch):
    _server(monkeypatch, {"approved": False, "opted_out": False, "first_contact": True, "kind": "contact"},
            send_status=202, send_body={"status": "pending", "owner_asked": "inbox"})
    out = sms.send_sms(to="+15551230000", body="hi")
    assert out["error"].startswith("I've asked Grant in the Windy Inbox")


def test_agent_auth_signs_the_url_without_query_or_fragment():
    headers, code = sms.agent_auth("https://api.windytext.com/sms/recipient?to=%2B1555#x", "get")
    assert code is None
    assert headers == {"Authorization": "Bearer tok-windy-telephony",
                       "DPoP": "proof-GET-https://api.windytext.com/sms/recipient"}
