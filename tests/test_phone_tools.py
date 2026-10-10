"""phone-tools.v1 (windy-contracts schema/chat/phone-tools.v1.json): the owner's phone as the agent's hands.

No network: Matrix REST is replaced by a fake that records calls and answers from canned room state/relations.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from windyfly.channels import phone_tools as pt

CONTRACT = Path(pt.__file__).parent / "contracts" / "phone-tools.v1.json"
# sha256 of the vendored windy-contracts schema/chat/phone-tools.v1.json (1.0.0, windy-contracts 746a2b2).
PHONE_TOOLS_V1_SHA256 = "243b5b72470d2160bdf5809312d94cabe58597334670d7d50e4d9a7e9be6e0d0"

OWNER = "@owner:chat.test"
ME = "@agent_et26-test-aaaa:chat.test"
ROOM = "!dm:chat.test"
NOW = 1_800_000_000.0


def _iso(ts: float) -> str:
    return pt._iso(ts)


class FakeMatrix:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.state: list[dict[str, Any]] = []
        self.relations: dict[str, list[dict[str, Any]]] = {}
        self.next_event = 0

    def __call__(self, method: str, path: str, body: Any = None, *, version: str = "v3") -> tuple[int, Any]:
        self.calls.append((method, path, body))
        if method == "GET" and path.endswith("/state"):
            return 200, self.state
        if method == "GET" and "/relations/" in path:
            event_id = path.split("/relations/")[1].split("/")[0].replace("%24", "$")
            return 200, {"chunk": self.relations.get(event_id, [])}
        if method == "PUT" and f"/send/{pt.REQUEST_TYPE}/" in path:
            self.next_event += 1
            return 200, {"event_id": f"$req{self.next_event}"}
        if method == "PUT" and "/redact/" in path:
            return 200, {"event_id": "$redaction"}
        return 404, {}

    def redacted(self) -> list[str]:
        return [path.split("/redact/")[1].split("/")[0].replace("%24", "$")
                for method, path, _ in self.calls if "/redact/" in path]


@pytest.fixture
def mx(monkeypatch: pytest.MonkeyPatch) -> FakeMatrix:
    fake = FakeMatrix()
    monkeypatch.setenv("WINDY_PHONE_TOOLS", "1")
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.test")
    monkeypatch.setenv("MATRIX_BOT_TOKEN", "t")
    monkeypatch.setenv("MATRIX_BOT_USER", ME)
    monkeypatch.setattr(pt, "_mx", fake)
    monkeypatch.setattr(pt, "_now", lambda: NOW)
    pt._pending.clear()
    pt._session_targets.clear()
    pt._thread.target = None
    fake.state = [
        {"type": "m.room.member", "state_key": OWNER, "content": {"membership": "join"}},
        {"type": "m.room.member", "state_key": ME, "content": {"membership": "join"}},
        _phone_state("dev_aaaaaaaa", NOW + 180),
    ]
    return fake


def _phone_state(device: str, expires: float, sender: str = OWNER,
                 tools: tuple[str, ...] = ("contacts.search", "sms.compose")) -> dict[str, Any]:
    return {"type": pt.STATE_TYPE, "state_key": device, "sender": sender,
            "content": {"device_id": device, "platform": "ios", "tools": list(tools), "expires_at": _iso(expires)}}


def _schemas() -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": n}} for n in ("web_search", *pt.TOOL_NAMES)]


# ── the contract ────────────────────────────────────────────────────────────────────────────────

def test_the_vendored_contract_is_the_pinned_one() -> None:
    assert hashlib.sha256(CONTRACT.read_bytes()).hexdigest() == PHONE_TOOLS_V1_SHA256


def test_limits_and_event_types_match_the_contract() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    limits, runtime = contract["x-limits"], contract["x-runtime"]
    assert pt.REQUEST_START_S == limits["request_start_s"] == runtime["request_start_s"]
    assert pt.RESULT_WAIT_S == limits["result_wait_s"] == runtime["result_wait_s"]
    assert pt.START_GRACE_S == limits["start_grace_s"]
    assert set(pt.TOOL_NAMES.values()) == set(contract["$defs"]["ToolName"]["enum"])
    assert runtime["offer_tools_on"] == "owner_turn_only" and runtime["queue_when_offline"] is False
    events = " ".join(e["event"] for e in contract["x-events"])
    for etype in (pt.STATE_TYPE, pt.REQUEST_TYPE, pt.STARTED_TYPE, pt.RESULT_TYPE):
        assert etype in events


# ── which phone is online ───────────────────────────────────────────────────────────────────────

def test_dark_by_default(monkeypatch: pytest.MonkeyPatch, mx: FakeMatrix) -> None:
    monkeypatch.delenv("WINDY_PHONE_TOOLS")
    assert pt.pick_phone(ROOM, OWNER) is None
    assert mx.calls == []


def test_a_live_owner_phone_is_picked(mx: FakeMatrix) -> None:
    target = pt.pick_phone(ROOM, OWNER)
    assert target is not None and target.device_id == "dev_aaaaaaaa"
    assert target.tools == {"contacts.search", "sms.compose"}


@pytest.mark.parametrize("change", ["encrypted", "third_member", "not_owner", "expired", "too_far", "no_tools"])
def test_no_phone_unless_every_room_check_passes(mx: FakeMatrix, change: str) -> None:
    if change == "encrypted":
        mx.state.append({"type": "m.room.encryption", "state_key": "", "content": {}})
    elif change == "third_member":
        mx.state.append({"type": "m.room.member", "state_key": "@other:chat.test", "content": {"membership": "join"}})
    elif change == "not_owner":
        mx.state[2] = _phone_state("dev_aaaaaaaa", NOW + 180, sender="@other:chat.test")
    elif change == "expired":
        mx.state[2] = _phone_state("dev_aaaaaaaa", NOW - 1)
    elif change == "too_far":
        mx.state[2] = _phone_state("dev_aaaaaaaa", NOW + pt.MAX_BELIEVED_TTL_S + 60)
    elif change == "no_tools":
        mx.state[2] = _phone_state("dev_aaaaaaaa", NOW + 180, tools=())
    assert pt.pick_phone(ROOM, OWNER) is None


def test_the_newest_expiry_wins(mx: FakeMatrix) -> None:
    mx.state.append(_phone_state("dev_bbbbbbbb", NOW + 170))
    mx.state.append(_phone_state("dev_cccccccc", NOW + 200))
    target = pt.pick_phone(ROOM, OWNER)
    assert target is not None and target.device_id == "dev_cccccccc"


# ── the model sees phone tools only on a turn with a live phone ─────────────────────────────────

def test_no_phone_no_phone_tools(mx: FakeMatrix) -> None:
    names = [s["function"]["name"] for s in pt.filter_tools(_schemas(), "s1")]
    assert names == ["web_search"]


def test_only_the_tools_the_phone_offers(mx: FakeMatrix) -> None:
    mx.state[2] = _phone_state("dev_aaaaaaaa", NOW + 180, tools=("contacts.search",))
    pt.mark_turn("s1", pt.pick_phone(ROOM, OWNER))
    names = [s["function"]["name"] for s in pt.filter_tools(_schemas(), "s1")]
    assert names == ["web_search", "phone_contacts_search"]
    pt.clear_turn("s1")
    assert [s["function"]["name"] for s in pt.filter_tools(_schemas(), "s1")] == ["web_search"]


# ── a request goes out and the tool returns at once ─────────────────────────────────────────────

def test_without_a_phone_the_tool_says_so(mx: FakeMatrix) -> None:
    assert pt.contacts_search(query="Whitmer") == {"ok": False, "error": "no_phone_online"}
    assert not any(m == "PUT" for m, _, _ in mx.calls)


def _bind(mx: FakeMatrix) -> None:
    pt.mark_turn("s1", pt.pick_phone(ROOM, OWNER))
    pt.filter_tools(_schemas(), "s1")


def test_a_request_matches_the_contract(mx: FakeMatrix) -> None:
    from jsonschema import Draft202012Validator

    _bind(mx)
    out = pt.contacts_search(query="Whitmer", field="phone", limit=20)
    assert out["ok"] is True and out["status"] == "asked_phone"
    method, path, body = [c for c in mx.calls if c[0] == "PUT"][0]
    assert f"/send/{pt.REQUEST_TYPE}/" in path
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    validator = Draft202012Validator({"$defs": contract["$defs"], "$ref": "#/$defs/ToolRequest"})
    assert list(validator.iter_errors(body)) == []
    assert pt._parse_ts(body["expires_at"]) - pt._parse_ts(body["issued_at"]) == pt.REQUEST_START_S
    assert body["device_id"] == "dev_aaaaaaaa"


def test_sms_args_keep_only_name_and_phone(mx: FakeMatrix) -> None:
    _bind(mx)
    pt.sms_compose(recipients=[{"name": "Sam", "phone": "+15555550100", "note": "x"}], body="hi", mode="individual")
    body = [c for c in mx.calls if c[0] == "PUT"][0][2]
    assert body["args"]["recipients"] == [{"name": "Sam", "phone": "+15555550100"}]


# ── answers, timeouts and clean-up ──────────────────────────────────────────────────────────────

def _send(mx: FakeMatrix) -> pt.Pending:
    _bind(mx)
    pt.sms_compose(recipients=[{"name": "Sam", "phone": "+15555550100"}], body="hi")
    return pt.pending()[0]


def _ev(etype: str, request_id: str, event_id: str, sender: str = OWNER, **content: Any) -> dict[str, Any]:
    return {"type": etype, "event_id": event_id, "sender": sender,
            "content": {etype: {"id": request_id, **content}}}


def test_a_result_is_read_once_redacted_and_becomes_one_turn(mx: FakeMatrix) -> None:
    p = _send(mx)
    mx.relations[p.event_id] = [
        _ev(pt.STARTED_TYPE, p.request_id, "$started"),
        _ev(pt.RESULT_TYPE, p.request_id, "$result", ok=True,
            result={"recipients": [{"phone": "+15555550100", "status": "sent"}]}),
    ]
    out = pt.poll_once()
    assert len(out) == 1
    room, owner, text = out[0]
    assert (room, owner) == (ROOM, OWNER)
    assert '"status":"sent"' in text and p.request_id in text
    assert set(mx.redacted()) == {"$result", "$started", p.event_id}
    assert pt.pending() == []
    assert pt.poll_once() == []


def test_a_strangers_result_is_ignored(mx: FakeMatrix) -> None:
    p = _send(mx)
    mx.relations[p.event_id] = [_ev(pt.RESULT_TYPE, p.request_id, "$x", sender="@other:chat.test", ok=True,
                                    result={"recipients": []})]
    assert pt.poll_once() == []  # the relations answer holds only a stranger's event
    assert [x.request_id for x in pt.pending()] == [p.request_id]
    assert mx.redacted() == []


def test_never_picked_up_is_a_timeout_that_ran_nothing(mx: FakeMatrix, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _send(mx)
    monkeypatch.setattr(pt, "_now", lambda: NOW + pt.REQUEST_START_S + pt.START_GRACE_S + 1)
    (_, _, text), = pt.poll_once()
    assert "did not pick it up" in text and "outcome unknown" not in text
    assert p.event_id in mx.redacted()


def test_started_but_silent_is_outcome_unknown(mx: FakeMatrix, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _send(mx)
    mx.relations[p.event_id] = [_ev(pt.STARTED_TYPE, p.request_id, "$started")]
    monkeypatch.setattr(pt, "_now", lambda: NOW + pt.REQUEST_START_S + pt.START_GRACE_S + 1)
    assert pt.poll_once() == []  # started: keep waiting
    monkeypatch.setattr(pt, "_now", lambda: NOW + pt.RESULT_WAIT_S + 1)
    (_, _, text), = pt.poll_once()
    assert "outcome unknown" in text and "not sent" not in text
    assert {"$started", p.event_id} <= set(mx.redacted())


def test_a_late_result_after_a_timeout_is_redacted_unread(mx: FakeMatrix, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _send(mx)
    monkeypatch.setattr(pt, "_now", lambda: NOW + pt.RESULT_WAIT_S + 1)
    assert len(pt.poll_once()) == 1
    mx.relations[p.event_id] = [_ev(pt.RESULT_TYPE, p.request_id, "$late", ok=True, result={"recipients": []})]
    assert pt.poll_once() == []  # no second turn
    assert "$late" in mx.redacted() and pt.pending() == []
