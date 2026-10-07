"""windyfly's Windy Calendar tools (Agentic Calendar K1): wire, plain refusals, no retry loops."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from windyfly.agent import service_auth, windy_calendar as wc
from windyfly.agent.capabilities.descriptor import Band, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.agent.capabilities.windy_calendar import register_windy_calendar_capabilities


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    return r


class _Log(list):
    def __init__(self) -> None:
        super().__init__()
        self.box: dict = {"seq": [_resp(200, {"ok": True, "result": {}})]}


@pytest.fixture
def calls(monkeypatch):
    monkeypatch.setenv(wc.ENV_FLAG, "1")
    monkeypatch.delenv("WINDY_CALENDAR_URL", raising=False)
    monkeypatch.setattr(service_auth, "agent_headers", lambda aud, m, u: {"Authorization": f"Bearer t-{aud}", "DPoP": "p"})
    monkeypatch.setattr(service_auth, "forget_token", lambda: None)
    log = _Log()
    box = log.box

    def fake(url, **kw):
        log.append((url, kw))
        seq = box["seq"]
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr("httpx.post", fake)
    return log


def _answer(calls, *responses):
    calls.box["seq"][:] = list(responses)


def _cap(cid):
    r = CapabilityRegistry()
    register_windy_calendar_capabilities(r)
    return r.get(cid)


def test_dark_by_default(monkeypatch):
    monkeypatch.delenv(wc.ENV_FLAG, raising=False)
    r = CapabilityRegistry()
    register_windy_calendar_capabilities(r)
    assert r.get("windy_calendar.availability") is None
    assert wc.invoke("get_booking_page")["ok"] is False


def test_all_tools_are_owner_only_and_writes_audited(calls):
    for cid in ("availability", "appointments", "booking_link", "book", "block"):
        assert _cap(f"windy_calendar.{cid}").band_required == Band.OWNER
    for cid in ("book", "block"):
        c = _cap(f"windy_calendar.{cid}")
        assert c.tier == Tier.EXTERNAL_EFFECT and c.audit_required


def test_availability_wire_and_count_default(calls):
    _answer(calls, _resp(200, {"ok": True, "result": {"slots": [{"startsAtUtc": "a", "endsAtUtc": "b"}]}}))
    out = _cap("windy_calendar.availability").handler(from_date="2026-10-12", to_date="2026-10-18")
    url, kw = calls[0]
    assert url == "https://windycalendar.com/invoke"
    assert kw["json"] == {"name": "get_availability", "arguments": {"from": "2026-10-12", "to": "2026-10-18", "count": 20}}
    assert kw["headers"]["DPoP"] == "p"
    assert out["ok"] and out["slots"][0]["startsAtUtc"] == "a"


def test_availability_caps_slots(calls):
    slots = [{"startsAtUtc": str(i), "endsAtUtc": str(i)} for i in range(80)]
    _answer(calls, _resp(200, {"ok": True, "result": {"slots": slots}}))
    out = _cap("windy_calendar.availability").handler(from_date="2026-10-12", to_date="2026-10-18", count=200)
    assert calls[0][1]["json"]["arguments"]["count"] == 50
    assert len(out["slots"]) == 50 and out["truncated"] is True


@pytest.mark.parametrize("result", ["https://windycalendar.com/book/c-abc", {"url": "https://windycalendar.com/book/c-abc"},
                                    {"booking_url": "https://windycalendar.com/book/c-abc"}])
def test_booking_link_comes_from_the_tool(calls, result):
    _answer(calls, _resp(200, {"ok": True, "result": result}))
    assert _cap("windy_calendar.booking_link").handler()["booking_link"] == "https://windycalendar.com/book/c-abc"


def test_booking_link_missing_is_plain(calls):
    _answer(calls, _resp(200, {"ok": True, "result": {}}))
    assert "isn't ready" in _cap("windy_calendar.booking_link").handler()["say"]


def test_book_sends_idempotency_key_and_reports_slot_taken(calls):
    _answer(calls, _resp(200, {"ok": True, "result": {"outcome": "slot_taken", "next_slots": ["x"]}}))
    out = _cap("windy_calendar.book").handler(starts_at_utc="2026-10-12T15:00:00Z", booker_name="A", booker_email="a@b.c")
    args = calls[0][1]["json"]["arguments"]
    assert args["idempotency_key"] and calls[0][1]["json"]["name"] == "book_appointment"
    assert out["outcome"] == "slot_taken" and out["next_slots"] == ["x"]


def test_confirm_required_waits_and_is_not_retried(calls):
    _answer(calls, _resp(200, {"ok": False, "error": "confirm_required", "confirmation": {"id": "c1", "waiting_in": "inbox"}}))
    out = wc.invoke("cancel_appointment", {"appointment_id": "1"}, write=True)
    assert out["pending_owner"] is True and out["confirmation_id"] == "c1" and len(calls) == 1
    assert "will not ask again" in out["say"]


@pytest.mark.parametrize("status,body,write,needle", [
    (403, {"ok": False, "error": "denied", "reason": "agent_blocked"}, False, "blocked"),
    (403, {"ok": False, "error": "denied", "reason": "no_calendar"}, False, "isn't a Windy Calendar"),
    (403, {"ok": False, "error": "denied", "reason": "insufficient_band"}, False, "can't see"),
    (403, {"ok": False, "error": "denied", "reason": "untrusted_write"}, True, "not trusted enough"),
    (400, {"ok": False, "error": "rate_limited", "reason": "own_calendar_daily_cap"}, True, "today's limit"),
    (400, {"ok": False, "error": "invalid_arguments", "reason": "impossible_time"}, True, "doesn't work"),
    (503, {"ok": False, "error": "auth_unavailable"}, False, "couldn't reach"),
    (404, {"ok": False, "error": "unknown_tool"}, False, "can't do that yet"),
])
def test_refusals_in_plain_words(calls, status, body, write, needle):
    _answer(calls, _resp(status, body))
    out = wc.invoke("x", write=write)
    assert out["ok"] is False and needle in out["say"] and len(calls) == 1
    assert "Mind" not in out["say"] and "EPT" not in out["say"]


def test_expired_token_remints_once(calls, monkeypatch):
    forgot = []
    monkeypatch.setattr(service_auth, "forget_token", lambda: forgot.append(1))
    _answer(calls, _resp(401, {"error": "unauthorized", "reason": "expired"}),
            _resp(200, {"ok": True, "result": "https://x/book/c"}))
    assert wc.invoke("get_booking_page")["ok"] is True
    assert len(calls) == 2 and forgot == [1]


def test_persistent_401_stops_after_one_retry(calls):
    _answer(calls, _resp(401, {"error": "unauthorized", "reason": "expired"}))
    out = wc.invoke("get_booking_page")
    assert out["ok"] is False and len(calls) == 2 and "sign in" in out["say"]


def test_no_token_and_network_errors_are_plain(calls, monkeypatch):
    def boom(aud, m, u):
        raise service_auth.ServiceAuthError("no_key")

    monkeypatch.setattr(service_auth, "agent_headers", boom)
    assert "sign in" in wc.invoke("get_booking_page")["say"]


def test_url_override(calls, monkeypatch):
    monkeypatch.setenv("WINDY_CALENDAR_URL", "https://cal.example/")
    wc.invoke("get_booking_page")
    assert calls[0][0] == "https://cal.example/invoke"


def test_non_owner_band_does_not_see_the_tools(calls):
    r = CapabilityRegistry()
    register_windy_calendar_capabilities(r)
    assert not [c for c in r.list_for_band(Band.TRUSTED) if c.id.startswith("windy_calendar.")]
    assert len([c for c in r.list_for_band(Band.OWNER) if c.id.startswith("windy_calendar.")]) == 5


@pytest.mark.parametrize("result", [{"url": "https://windycalendar.com/book/c-abc", "listed": False},
                                    {"url": "https://windycalendar.com/book/c-abc", "state": "unlisted"}])
def test_booking_link_off_is_said_plainly(calls, result):
    _answer(calls, _resp(200, {"ok": True, "result": result}))
    out = _cap("windy_calendar.booking_link").handler()
    assert out["sharing_off"] is True and "turned off" in out["say"]
