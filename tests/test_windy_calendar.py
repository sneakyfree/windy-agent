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


def _registered() -> CapabilityRegistry:
    """Register with the boot probe answered OK (the probe is tested on its own below)."""
    real = wc.invoke
    wc.invoke = lambda *a, **k: {"ok": True, "result": {}}  # type: ignore[assignment]
    try:
        r = CapabilityRegistry()
        register_windy_calendar_capabilities(r)
    finally:
        wc.invoke = real  # type: ignore[assignment]
    return r


def _cap(cid):
    return _registered().get(cid)


def test_dark_by_default(monkeypatch):
    monkeypatch.delenv(wc.ENV_FLAG, raising=False)
    r = CapabilityRegistry()
    register_windy_calendar_capabilities(r)
    assert r.get("windy_calendar.availability") is None
    assert wc.invoke("get_booking_page")["ok"] is False


def test_all_tools_are_trusted_band_and_writes_audited(calls):
    for cid in ("availability", "appointments", "booking_link", "book", "block"):
        assert _cap(f"windy_calendar.{cid}").band_required == Band.TRUSTED
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
    assert out["say"] == "That needs my owner's OK. It is waiting in the Windy Inbox."


@pytest.mark.parametrize("status,body,write,needle", [
    (403, {"ok": False, "error": "denied", "reason": "agent_blocked"}, False, "blocked"),
    (403, {"ok": False, "error": "denied", "reason": "no_calendar"}, False, "isn't a Windy Calendar"),
    (403, {"ok": False, "error": "denied", "reason": "insufficient_band"}, False, "can't see"),
    (403, {"ok": False, "error": "denied", "reason": "untrusted_write"}, True, "not trusted enough"),
    (400, {"ok": False, "error": "rate_limited", "reason": "own_calendar_daily_cap"}, True, "daily limit"),
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
    r = _registered()
    assert not [c for c in r.list_for_band(Band.USER) if c.id.startswith("windy_calendar.")]
    trusted = {c.id for c in r.list_for_band(Band.TRUSTED) if c.id.startswith("windy_calendar.")}
    assert len(trusted) == 5 and "windy_calendar.create_meeting" not in trusted
    assert r.get("windy_calendar.create_meeting") in r.list_for_band(Band.OWNER)


@pytest.mark.parametrize("result", [{"url": "https://windycalendar.com/book/c-abc", "listed": False},
                                    {"url": "https://windycalendar.com/book/c-abc", "state": "unlisted"}])
def test_booking_link_off_is_said_plainly(calls, result):
    _answer(calls, _resp(200, {"ok": True, "result": result}))
    out = _cap("windy_calendar.booking_link").handler()
    assert out["sharing_off"] is True and "turned off" in out["say"] and "settings" not in out["say"]


# ── Hub's hold on #473: nothing but a Chat-verified same-owner sibling may resolve to TRUSTED ──────

def test_only_a_listed_same_owner_sibling_resolves_to_trusted(monkeypatch, tmp_path):
    from windyfly.agent import teams
    from windyfly.channels import identity

    owner = "@owner:chat.example"
    sib = "@agent_et26-sib0-0002:chat.windychat.ai"
    monkeypatch.setenv("WINDY_OWNER_IDS", f"matrix:{owner}")
    monkeypatch.setenv("WINDY_OWNER_BINDINGS_PATH", str(tmp_path / "o.json"))
    monkeypatch.setenv("WINDY_TEAMS", "1")
    teams._reset_for_tests()
    teams._remember([{"name": "Sib", "passport": "ET26-SIB0-0002", "matrix_id": sib}])
    outcomes = {
        "owner": identity.resolve_band("matrix", owner),
        "human_contact": identity.resolve_band("matrix", "@friend:chat.example"),
        "trusted_looking_human": identity.resolve_band("matrix", "@trusted_friend:chat.example"),
        "stranger_agent": identity.resolve_band("matrix", "@agent_et26-evil-0003:chat.windychat.ai"),
        "unknown_platform_user": identity.resolve_band("telegram", "12345"),
        "sibling": identity.resolve_band("matrix", sib),
    }
    assert [k for k, v in outcomes.items() if v == Band.TRUSTED] == ["sibling"]
    assert outcomes["owner"] == Band.OWNER
    teams._reset_for_tests()


def test_no_other_module_issues_the_trusted_band():
    """Hub: if anything else could return TRUSTED, calendar book/block would open to it. Today the only
    place that ASSIGNS the band is channels/identity.py (the sibling branch)."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "windyfly"
    # a band VALUE being produced for a sender: `band = Band.TRUSTED` or `return Band.TRUSTED`
    pat = re.compile(r"\bband\s*=\s*Band\.TRUSTED\b|\breturn\s+Band\.TRUSTED\b")
    offenders = sorted(str(p.relative_to(src)) for p in src.rglob("*.py")
                       if pat.search(p.read_text("utf-8")) and p.name != "identity.py")
    assert offenders == [], offenders


# ── Calendar's contract: mode B applies to GET /tools too (a DPoP proof on every request) ─────────

@pytest.mark.parametrize("aud,method,dpop", [
    ("windy-calendar", "GET", True), ("windy-calendar", "POST", True),
    ("windy-chat", "GET", True), ("windy-mind", "GET", False), ("windy-mind", "POST", True),
    ("windy-vault", "GET", False),
])
def test_dpop_proof_rules_per_service(monkeypatch, aud, method, dpop):
    monkeypatch.setattr("windyfly.eternitas.agent_keys.request_agent_token", lambda a: {"token": "t"})
    proofs = []
    monkeypatch.setattr("windyfly.eternitas.agent_keys.service_dpop",
                        lambda m, u: proofs.append((m, u)) or "proof")
    url = "https://windycalendar.com/tools"
    h = service_auth.agent_headers(aud, method, url)
    assert ("DPoP" in h) is dpop
    assert (proofs == [(method, url)]) is dpop


# ── registration needs Calendar to answer (tool budget: no calendar, no tools) ──────────────────

@pytest.mark.parametrize("probe", [
    _resp(403, {"ok": False, "error": "denied", "reason": "no_calendar"}),
    _resp(403, {"ok": False, "error": "denied", "reason": "not_linked"}),
    _resp(503, {"ok": False, "error": "unavailable"}),
])
def test_no_tools_when_calendar_does_not_answer(calls, probe):
    _answer(calls, probe)
    r = CapabilityRegistry()
    register_windy_calendar_capabilities(r)
    assert not [c for c in r.all() if c.id.startswith("windy_calendar.")]
    assert calls[0][1]["json"]["name"] == "get_booking_page"


def test_tools_register_when_calendar_answers(calls):
    _answer(calls, _resp(200, {"ok": True, "result": {"url": "https://windycalendar.com/book/c-abc"}}))
    r = CapabilityRegistry()
    register_windy_calendar_capabilities(r)
    assert r.get("windy_calendar.create_meeting") is not None and len(calls) == 1


# ── create_meeting: only asks; the owner approves in the Inbox; counts only in logs ─────────────

def test_create_meeting_only_asks_the_owner(calls, caplog):
    import logging

    _answer(calls, _resp(200, {"ok": False, "error": "confirm_required", "reason": "invites_need_owner",
                               "confirmation": {"id": "conf_1", "waiting_in": "inbox"}}))
    cap = _cap("windy_calendar.create_meeting")
    with caplog.at_level(logging.INFO):
        out = cap.handler(starts_at_utc="2026-10-20T15:00:00Z", duration_minutes=30, title="Plan the party",
                          location="https://meet.example/abc",
                          invitees=[{"email": "ann@example.com", "name": "Ann"}, {"email": "bo@example.com"}])
    sent = calls[0][1]["json"]
    assert sent["name"] == "create_meeting"
    assert sent["arguments"]["invitees"] == [{"email": "ann@example.com", "name": "Ann"}, {"email": "bo@example.com"}]
    assert sent["arguments"]["duration_minutes"] == 30
    assert out == {"ok": False, "pending_owner": True, "confirmation_id": "conf_1",
                   "say": "That needs my owner's OK. It is waiting in the Windy Inbox."}
    assert len(calls) == 1  # never retried, never approves itself
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "n_invitees=2 outcome=pending_owner" in logged
    for private in ("ann@example.com", "bo@example.com", "Plan the party", "meet.example", "Ann"):
        assert private not in logged


def test_create_meeting_is_not_in_the_args_audit(calls):
    cap = _cap("windy_calendar.create_meeting")
    assert cap.tier == Tier.EXTERNAL_EFFECT and cap.band_required == Band.OWNER
    assert cap.audit_required is False  # the generic audit row would store invitee addresses


def test_create_meeting_schema_matches_the_contract_limits(calls):
    props = _cap("windy_calendar.create_meeting").input_schema["properties"]
    assert props["duration_minutes"]["minimum"] == 15 and props["duration_minutes"]["maximum"] == 240
    assert props["invitees"]["minItems"] == 1 and props["invitees"]["maxItems"] == 10
