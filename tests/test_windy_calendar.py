"""windyfly's Windy Calendar tools (Agentic Calendar K1): wire, plain refusals, no retry loops."""

from __future__ import annotations

import re
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


def test_book_in_progress_is_still_working_never_booked(calls):
    # windy-calendar #127: while the first call with this key runs, Calendar answers ok:true + outcome in_progress
    # whatever the arguments. Never "booked", never "made with different details".
    _answer(calls, _resp(200, {"ok": True, "result": {"outcome": "in_progress", "retry_after_seconds": 5}}))
    out = _cap("windy_calendar.book").handler(starts_at_utc="2026-10-12T15:00:00Z", booker_name="A",
                                              booker_email="a@b.c", idempotency_key="k1")
    assert out["ok"] is False and out["outcome"] == "in_progress" and out["retry_after_seconds"] == 5
    assert out["say"] == "I'm still making that booking. Ask me again in a moment."
    assert "different details" not in out["say"] and len(calls) == 1  # one call: no retry loop of its own


def test_add_event_in_progress_is_still_working(add_on, calls):
    _answer(calls, _resp(200, {"ok": True, "result": {"outcome": "in_progress", "retry_after_seconds": 5}}))
    out = _cap("windy_calendar.add_event").handler(title="t", starts_at_utc="2026-10-12T15:00:00Z")
    assert out["ok"] is False and out["say"] == "I'm still adding that one. Ask me again in a moment."


def test_the_same_booking_always_sends_the_same_key(calls, monkeypatch):
    """A retry (the model asks twice, a timeout) returns the FIRST booking, never "just taken" (Calendar 11-04)."""
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-BOOK-0001")
    book = _cap("windy_calendar.book").handler
    base = {"starts_at_utc": "2026-10-12T15:00:00Z", "booker_name": "A", "booker_email": "Ann@Example.com"}

    def key(**kw):
        _answer(calls, _resp(200, {"ok": True, "result": {"outcome": "booked"}}))
        book(**{**base, **kw})
        return calls[-1][1]["json"]["arguments"]["idempotency_key"]

    first = key()
    assert re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", first)                 # Calendar's idempotency_key pattern
    assert key() == first                                                   # a retry: the same key
    assert key(booker_email=" ann@example.COM ") == first                   # the email, lowercased
    assert key(booker_name="Ann B", booker_phone="+15550100", booker_note="x", booker_tz="America/Denver") == first
    assert key(starts_at_utc="2026-10-12T16:00:00Z") != first               # another time: another booking
    assert key(booker_email="bob@example.com") != first                     # another booker: another booking
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-BOOK-0002")
    assert key() != first                                                   # another agent: another key


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
    (403, {"ok": False, "error": "denied", "reason": "own_agent_only"}, True, "owner's own agent do that"),
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


def test_an_invalid_token_remints_once_too(calls, monkeypatch):
    forgot = []
    monkeypatch.setattr(service_auth, "forget_token", lambda: forgot.append(1))
    _answer(calls, _resp(401, {"error": "unauthorized", "reason": "invalid"}),
            _resp(200, {"ok": True, "result": "https://x/book/c"}))
    assert wc.invoke("get_booking_page")["ok"] is True
    assert len(calls) == 2 and forgot == [1]


def test_any_other_401_reason_is_not_reminted(calls, monkeypatch):
    forgot = []
    monkeypatch.setattr(service_auth, "forget_token", lambda: forgot.append(1))
    _answer(calls, _resp(401, {"error": "unauthorized", "reason": "revoked"}))
    assert wc.invoke("get_booking_page")["ok"] is False
    assert len(calls) == 1 and forgot == []


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
    """Hub: if anything else could return TRUSTED, calendar book/block would open to it. The places that ASSIGN
    the band: channels/identity.py (the sibling branch), and agent/goal_autorun.py's goal_band() ONLY (a background
    goal runs at TRUSTED, never OWNER: Boss BOARD 2026-11-07T19:26:03Z), where book/block pause the goal instead
    (test_a_goal_turn_never_books_or_blocks below)."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "windyfly"
    # a band VALUE being produced for a sender: `band = Band.TRUSTED` or `return Band.TRUSTED`
    pat = re.compile(r"\bband\s*=\s*Band\.TRUSTED\b|\breturn\s+Band\.TRUSTED\b")
    allowed = {"channels/identity.py": None, "agent/goal_autorun.py": "goal_band"}
    offenders = []
    for p in sorted(src.rglob("*.py")):
        rel, text = str(p.relative_to(src)), p.read_text("utf-8")
        hits = list(pat.finditer(text))
        if not hits or (rel in allowed and allowed[rel] is None):
            continue
        if rel in allowed:      # only inside the one named function, and only once
            fn = re.findall(r"^def (\w+)", text[:hits[0].start()], re.M)
            if len(hits) == 1 and fn and fn[-1] == allowed[rel]:
                continue
        offenders.append(rel)
    assert offenders == [], offenders


@pytest.mark.parametrize("cid", ["book", "block"])
def test_a_goal_turn_never_books_or_blocks(calls, cid):
    """A background goal at TRUSTED (goal_band) reaches book/block's band, but they are EXTERNAL_EFFECT: the goal
    pauses for the owner and Calendar is never called. A person's TRUSTED turn is unchanged."""
    import json

    from windyfly.agent.capabilities import CapabilityDenied
    from windyfly.agent.goal_autorun import goal_band
    from windyfly.agent.loop import _dispatch_tool_call_inner
    from windyfly.tools import bands

    cap = _cap(f"windy_calendar.{cid}")
    ran: list[str] = []

    class _One:
        def get(self, name):
            return cap if name == cap.id else None

        def invoke_sync(self, name, args, band):
            ran.append(name)
            return "ran"

    with bands.goal_turn():
        out = _dispatch_tool_call_inner(cap.id, "{}", None, _One(), goal_band(), CapabilityDenied)
        assert out == json.dumps({"error": bands.PAUSED_TEXT})
        assert bands.needs_owner() == cap.id
    assert ran == []
    assert _dispatch_tool_call_inner(cap.id, "{}", None, _One(), Band.TRUSTED, CapabilityDenied) == "ran"


# ── Calendar's contract: mode B applies to GET /tools too (a DPoP proof on every request) ─────────

@pytest.mark.parametrize("aud,method,dpop", [
    ("windy-calendar", "GET", True), ("windy-calendar", "POST", True),
    ("windy-chat", "GET", True), ("windy-mind", "GET", True), ("windy-mind", "POST", True),
    ("windy-vault", "GET", True),
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


def test_appointments_and_block_work_end_to_end(calls):
    """N5.19: both handlers put the right call on the wire and hand Calendar's answer back."""
    appts = [{"startsAtUtc": "2026-10-20T15:00:00Z", "bookerName": "Sam"}]
    _answer(calls, _resp(200, {"ok": True, "result": appts}))
    out = _cap("windy_calendar.appointments").handler(from_date="2026-10-19", to_date="2026-10-25", timezone="America/New_York")
    assert calls[-1][0] == "https://windycalendar.com/invoke"
    assert calls[-1][1]["json"] == {"name": "list_appointments",
                                    "arguments": {"timezone": "America/New_York", "from": "2026-10-19", "to": "2026-10-25"}}
    assert out == {"ok": True, "appointments": appts}

    _answer(calls, _resp(200, {"ok": True, "result": {"block_id": "b-1"}}))
    out = _cap("windy_calendar.block").handler(starts_at_utc="2026-10-21T13:00:00Z",
                                               ends_at_utc="2026-10-21T14:00:00Z", reason="dentist")
    sent = calls[-1][1]["json"]
    assert sent["name"] == "block_time"
    assert {k: sent["arguments"][k] for k in ("starts_at_utc", "ends_at_utc", "reason")} == {
        "starts_at_utc": "2026-10-21T13:00:00Z", "ends_at_utc": "2026-10-21T14:00:00Z", "reason": "dentist"}
    assert out == {"ok": True, "block_id": "b-1"}

    _answer(calls, _resp(403, {"ok": False, "error": "denied", "reason": "agent_blocked"}))
    refused = _cap("windy_calendar.block").handler(starts_at_utc="2026-10-21T13:00:00Z", ends_at_utc="2026-10-21T14:00:00Z")
    assert refused["ok"] is False


# P136-5 (Boss 2026-10-28): "no permission" came from tools that didn't say what they do. The words are pinned so a
# change goes back through Boss, Calendar's fact check and Hub #9.
# Boss 17:49Z: each agent's sentence is true for ITS tools. windyfly has create_meeting (owner approves in the Inbox;
# Calendar 17:51Z: an approved one creates the meeting and sends invites today), so the sentence offers it.
SAY_OWN_MEETING = ("I can't add an event just for you yet. I can set up a meeting with the people you name for you to "
                   "approve in your Windy Inbox, or hold that time so it can't be booked on your booking page.")


def test_book_says_it_is_for_another_person_and_what_to_tell_the_owner(calls):
    d = _cap("windy_calendar.book").description
    assert d.startswith("Books a time on the owner's calendar for another person: their name and email are required.")
    assert "It can't add an event or meeting of the owner's own." in d
    assert d.endswith(f'say: "{SAY_OWN_MEETING}"')
    # Calendar / Hub #9, 10-28. "Books an open slot" was untrue for the own agent; "Every other caller gets open
    # slots only" (Calendar's own-agent rule, #638) is true, so the ban is on the claim, not the words "open slot".
    for untrue in ("Books an open slot", "not an open slot is refused", "booking page does", "nobody",
                   "can't add meetings"):
        assert untrue not in d
    assert _cap("windy_calendar.create_meeting") is not None  # the sentence offers it, so it must be there


def test_block_says_what_it_holds_and_that_it_makes_no_meeting(calls):
    d = _cap("windy_calendar.block").description
    assert d.startswith("Holds a time range on the owner's calendar so it can't be booked on their booking page.")
    assert "It doesn't create a meeting or send an invite." in d
    assert "nobody" not in d


# ── windy_calendar.add_event (P136-5; tools-invoke.v1 1.3.0, windy-contracts #238) ─────────────────────────


from windyfly.agent.capabilities import audit  # noqa: E402
from windyfly.agent.capabilities import windy_calendar as wcal  # noqa: E402

EVENT = {"title": "Dentist", "starts_at_utc": "2026-11-05T15:00:00Z", "duration_minutes": 45,
         "location": "12 Main St", "note": "bring the card"}


@pytest.fixture
def add_on(calls, monkeypatch):
    monkeypatch.setenv(wcal.ADD_EVENT_FLAG, "1")
    return calls


def test_add_event_is_dark_without_its_own_flag(calls, monkeypatch):
    monkeypatch.delenv(wcal.ADD_EVENT_FLAG, raising=False)
    r = _registered()
    assert r.get("windy_calendar.block") is not None and r.get("windy_calendar.add_event") is None


def test_add_event_is_owner_only_audited_and_strict(add_on):
    c = _cap("windy_calendar.add_event")
    assert c.band_required == Band.OWNER and c.tier == Tier.EXTERNAL_EFFECT and c.audit_required and c.strict_args
    assert c.input_schema["required"] == ["title", "starts_at_utc", "duration_minutes"]
    assert "invitees" not in c.input_schema["properties"]  # an event of the owner's own: nobody else
    assert c.description.startswith("Adds an event of the owner's own to their calendar")


def test_add_event_wire_and_outcome_as_data(add_on):
    _answer(add_on, _resp(200, {"ok": True, "result": {"outcome": "added", "outside_open_hours": True}}))
    out = _cap("windy_calendar.add_event").handler(**EVENT)
    body = add_on[0][1]["json"]
    assert body["name"] == "add_event"
    assert {k: body["arguments"][k] for k in EVENT} == EVENT and body["arguments"]["idempotency_key"]
    assert out == {"ok": True, "outcome": "added", "outside_open_hours": True}


@pytest.mark.parametrize("outcome", ["overlaps_booking", "in_the_past", "too_far_ahead", "invalid_time",
                                     "not_enabled", "in_progress", "own_agent_only"])
def test_only_added_is_ok_every_other_outcome_is_a_refusal_in_fixed_words(add_on, outcome):
    """Calendar answers 200 for every outcome (windy-calendar #109); "ok" must mean the event IS on the calendar, so
    the model can never say "added" for in_the_past, an overlap or a switched-off Calendar."""
    _answer(add_on, _resp(200, {"ok": True, "result": {"outcome": outcome}}))
    out = _cap("windy_calendar.add_event").handler(**EVENT)
    assert out == {"ok": False, "outcome": outcome, "say": wcal.ADD_EVENT_SAY[outcome]}


def test_an_unknown_outcome_is_a_refusal_too(add_on):
    _answer(add_on, _resp(200, {"ok": True, "result": {"outcome": "something_new"}}))
    out = _cap("windy_calendar.add_event").handler(**EVENT)
    assert out["ok"] is False and out["say"].startswith("Windy Calendar gave an answer I don't understand")
    _answer(add_on, _resp(200, {"ok": True, "result": {}}))
    assert _cap("windy_calendar.add_event").handler(**EVENT)["ok"] is False


APPT = {"id": "a1", "kind": "event", "title": "Dentist", "starts_at_utc": "2026-11-05T15:00:00Z",
        "ends_at_utc": "2026-11-05T15:45:00Z", "location": "12 Main St"}
REPLAY = _resp(200, {"ok": True, "result": {"outcome": "added", "idempotent_replay": True, "appointment": APPT}})


def test_a_replay_names_the_event_in_the_owners_own_time(add_on):
    """ONE sentence for both runtimes (Boss 11-02, Chat's roster wording); the zone is Calendar's owner_timezone."""
    _answer(add_on, REPLAY, _resp(200, {"ok": True, "result": {"owner_timezone": "America/New_York", "slots": []}}))
    out = _cap("windy_calendar.add_event").handler(**EVENT)
    assert out["ok"] is True and out["appointment"] == APPT and out["idempotent_replay"] is True
    assert out["say"] == "That's already on your calendar: Dentist, Thu, Nov 5, 10:00 AM. I didn't add it a second time."
    probe = add_on[1][1]["json"]
    assert probe["name"] == "get_availability" and probe["arguments"]["from"] == probe["arguments"]["to"] == "2026-11-05"


@pytest.mark.parametrize("zone_answer", [
    _resp(200, {"ok": True, "result": {"slots": []}}),                           # no owner_timezone
    _resp(200, {"ok": True, "result": {"owner_timezone": "Mars/Olympus_Mons"}}),  # not an IANA zone
    _resp(503, {"ok": False, "error": "auth_unavailable"}),                       # Calendar didn't answer
])
def test_a_replay_without_a_readable_zone_uses_the_short_form(add_on, zone_answer):
    _answer(add_on, REPLAY, zone_answer)
    out = _cap("windy_calendar.add_event").handler(**EVENT)
    assert out["ok"] is True and out["say"] == wcal.ADD_EVENT_REPLAY_SHORT


@pytest.mark.parametrize("appt", [{"id": "a1"}, {"id": "a1", "starts_at_utc": "2026-11-05T15:00:00Z"},
                                  {"id": "a1", "title": "Dentist"}])
def test_a_replay_without_a_title_or_time_asks_nothing_more(add_on, appt):
    """Never "That's already on your calendar: , Thu ...": no title or no time = the short form, no zone read."""
    _answer(add_on, _resp(200, {"ok": True, "result": {"outcome": "added", "idempotent_replay": True,
                                                        "appointment": appt}}),
            _resp(200, {"ok": True, "result": {"owner_timezone": "America/New_York"}}))
    out = _cap("windy_calendar.add_event").handler(**EVENT)
    assert out["say"] == wcal.ADD_EVENT_REPLAY_SHORT and len(add_on) == 1


@pytest.mark.parametrize("starts,tz,want", [
    ("2026-11-05T15:00:00Z", "America/New_York", "Thu, Nov 5, 10:00 AM"),
    ("2026-11-05T23:30:00Z", "America/Los_Angeles", "Thu, Nov 5, 3:30 PM"),
    ("2026-11-06T05:00:00Z", "America/New_York", "Fri, Nov 6, 12:00 AM"),
    ("2026-11-05T06:30:00Z", "Asia/Kolkata", "Thu, Nov 5, 12:00 PM"),
    ("2026-07-04T16:05:00Z", "America/New_York", "Sat, Jul 4, 12:05 PM"),  # daylight time
])
def test_owner_when_matches_the_rosters_en_us_format(monkeypatch, starts, tz, want):
    monkeypatch.setattr(wc, "invoke", lambda *a, **k: {"ok": True, "result": {"owner_timezone": tz}})
    assert wcal.owner_when(starts) == want


def test_a_first_add_carries_no_replay_sentence(add_on):
    _answer(add_on, _resp(200, {"ok": True, "result": {"outcome": "added"}}))
    assert "say" not in _cap("windy_calendar.add_event").handler(**EVENT)


def test_a_refusal_is_the_clients_plain_sentence(add_on):
    _answer(add_on, _resp(429, {"ok": False, "error": "rate_limited"}))
    out = _cap("windy_calendar.add_event").handler(**EVENT)
    assert out["ok"] is False and out["say"] == "I've reached my daily limit for changing my owner's calendar."


def test_the_flag_off_at_call_time_sends_nothing(add_on, monkeypatch):
    c = _cap("windy_calendar.add_event")
    monkeypatch.setenv(wcal.ADD_EVENT_FLAG, "0")
    n = len(add_on)
    assert c.handler(**EVENT) == {"ok": False, "outcome": "not_enabled", "say": wcal.ADD_EVENT_SAY["not_enabled"]}
    assert len(add_on) == n


def test_the_audit_row_holds_when_and_how_long_only(add_on, monkeypatch):
    rows = []
    monkeypatch.setattr(audit, "record_action_start", lambda db, q, **k: rows.append(k))
    monkeypatch.setattr(audit, "record_action_end", lambda db, q, **k: None)
    r = _registered()
    audit.install_audit_hooks(r, db=None, write_queue=None)
    _answer(add_on, _resp(200, {"ok": True, "result": {"outcome": "added"}}))
    r.invoke_sync("windy_calendar.add_event", dict(EVENT), Band.OWNER)
    import json as _json
    assert [_json.loads(x["args_json"]) for x in rows] == [{"starts_at_utc": EVENT["starts_at_utc"],
                                                            "duration_minutes": 45}]


@pytest.mark.parametrize("band", [Band.TRUSTED, Band.USER])
def test_a_sibling_or_stranger_cannot_add_events(add_on, band):
    r = _registered()
    n = len(add_on)
    with pytest.raises(Exception):
        r.invoke_sync("windy_calendar.add_event", dict(EVENT), band)
    assert len(add_on) == n


@pytest.mark.parametrize("bad", [{"invitees": [{"email": "a@b.c"}]}, {"duration_minutes": 4},
                                 {"duration_minutes": 721}, {"title": ""}, {"starts_at_utc": "tomorrow"},
                                 {"duration_minutes": "45"}, {"duration_minutes": True}])
def test_arguments_outside_the_contract_are_refused_before_any_call(add_on, bad):
    r = _registered()
    n = len(add_on)
    with pytest.raises(Exception):
        r.invoke_sync("windy_calendar.add_event", {**EVENT, **bad}, Band.OWNER)
    assert len(add_on) == n


def test_the_same_event_twice_sends_the_same_key_and_a_different_one_does_not(add_on, monkeypatch):
    # Hub on #670: a retried or doubled add_event must dedupe at Calendar, not add a second copy.
    import re

    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-AAAA-BBBB")
    h = _cap("windy_calendar.add_event").handler
    h(**EVENT)
    h(**EVENT)
    h(**{**EVENT, "note": "a different note"})              # the same event, other words: still the same key
    h(**{**EVENT, "starts_at_utc": "2026-11-05T16:00:00Z"})  # another time: another event
    h(**{**EVENT, "title": "Dentist, again"})               # another title: another event
    h(**{**EVENT, "duration_minutes": 30})                  # another length: another event
    keys = [c[1]["json"]["arguments"]["idempotency_key"] for c in add_on]
    assert keys[0] == keys[1] == keys[2] and len(set(keys[2:])) == 4
    assert all(re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", k) for k in keys)  # Calendar's idempotency_key pattern
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-CCCC-DDDD")
    h(**EVENT)
    assert add_on[-1][1]["json"]["arguments"]["idempotency_key"] != keys[0]  # another agent: another key


def test_book_description_is_calendars_own_agent_rule(calls):
    """Grant's Build-131 note 9: the old text said a time outside the open slots is refused, which is untrue for the
    calendar's own agent after windy-calendar #96. The description is Calendar's own wording, facts only."""
    _answer(calls, _resp(200, {"ok": True, "result": "https://windycalendar.com/book/c-abc"}))
    text = _cap("windy_calendar.book").description
    assert "not an open slot is refused" not in text
    assert "own agent may book any future time up to two years ahead that overlaps no booking" in text
    assert "outside_open_hours, overlaps_blocked_time" in text and "Every other caller gets open slots only" in text


def test_book_passes_outside_open_hours_through(calls):
    _answer(calls, _resp(200, {"ok": True, "result": {"outcome": "booked", "outside_open_hours": True}}))
    out = _cap("windy_calendar.book").handler(starts_at_utc="2026-10-23T01:00:00Z", booker_name="A", booker_email="a@b.c")
    assert out["ok"] is True and out["outside_open_hours"] is True



# ── windy-contracts #275: a reused key with different details (422 idempotency_key_reused) ───────────────────────────

@pytest.mark.parametrize("tool,cap,args,words", [
    ("book_appointment", "windy_calendar.book",
     {"starts_at_utc": "2026-10-12T15:00:00Z", "booker_name": "A", "booker_email": "a@b.c"},
     "This booking was already made with different details; ask me to change that booking instead of booking again."),
    ("add_event", "windy_calendar.add_event",
     {"title": "Dentist", "starts_at_utc": "2026-10-12T15:00:00Z", "duration_minutes": 30},
     "That event is already on the calendar with different details; ask me to change it instead."),
])
def test_a_reused_key_with_other_details_is_a_true_sentence_never_slot_taken(calls, monkeypatch, tool, cap, args, words):
    monkeypatch.setenv("WINDY_CALENDAR_ADD_EVENT", "1")
    _answer(calls, _resp(422, {"ok": False, "error": "idempotency_key_reused"}))
    out = wc.invoke(tool, dict(args, idempotency_key="k-12345678"), write=True)
    assert out == {"ok": False, "code": "idempotency_key_reused", "say": words}
    assert len(calls) == 1                                                  # never retried: nothing was made
