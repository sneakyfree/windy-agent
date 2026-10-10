"""Phone tools: the owner's phone as the agent's hands (windy-contracts phone-tools.v1, chat owner).

Events in the owner's unencrypted 1:1 DM. The phone announces itself with an ``ai.windy.phone_tools`` state
event (state_key = device_id) while the app is in the foreground. On an OWNER turn in that DM, while one entry is
live, the model sees two tools (dots are not allowed in function names):
phone_contacts_search <-> contacts.search and phone_sms_compose <-> sms.compose.

A tool call sends ``ai.windy.tool_request`` and returns at once: nio runs sync callbacks one at a time, so a turn
that waited for the phone would freeze every room. A poller reads ``/relations/{request}/m.reference`` for
``ai.windy.tool_started`` / ``ai.windy.tool_result`` from the owner, then runs ONE follow-up owner turn carrying
the result (facts only) or the timeout. After reading, it redacts the result, the started event and its own request.

Dark: needs WINDY_PHONE_TOOLS=1. Not live for anyone until privacy v24 + Grant's word + a lab-phone proof (Boss).
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

STATE_TYPE = "ai.windy.phone_tools"
REQUEST_TYPE = "ai.windy.tool_request"
STARTED_TYPE = "ai.windy.tool_started"
RESULT_TYPE = "ai.windy.tool_result"

# x-limits of phone-tools.v1
REQUEST_START_S = 120
START_GRACE_S = 15
RESULT_WAIT_S = 600
MAX_BELIEVED_TTL_S = 15 * 60  # an expires_at further out than this is not believed (clock skew / a stuck phone)
LATE_RESULT_KEEP_S = 600  # how long a timed-out request is still checked once for a late result

TOOL_NAMES = {"phone_contacts_search": "contacts.search", "phone_sms_compose": "sms.compose"}
_TIMEOUT_S = 10.0


def enabled() -> bool:
    return os.environ.get("WINDY_PHONE_TOOLS", "").strip() == "1"


# ── Matrix REST as this agent ────────────────────────────────────────────────────────────────────

def _matrix() -> tuple[str, str]:
    return os.environ.get("MATRIX_HOMESERVER", "").rstrip("/"), os.environ.get("MATRIX_BOT_TOKEN", "")


def _me() -> str:
    return os.environ.get("MATRIX_BOT_USER", "").strip()


def _q(part: str) -> str:
    return urllib.parse.quote(part, safe="")


def _mx(method: str, path: str, body: dict[str, Any] | None = None, *, version: str = "v3") -> tuple[int, Any]:
    """One Matrix client call. Raises httpx.HTTPError when the server can't be reached."""
    homeserver, token = _matrix()
    resp = httpx.request(method, f"{homeserver}/_matrix/client/{version}/{path}",
                         headers={"Authorization": f"Bearer {token}"}, json=body, timeout=_TIMEOUT_S)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ── Which phone (if any) is online in this room ──────────────────────────────────────────────────

@dataclass
class Target:
    room_id: str
    owner: str
    device_id: str
    tools: frozenset[str]


def pick_phone(room_id: str, owner: str) -> Target | None:
    """The owner's live phone in this room, or None. Room checks: no encryption, joined == {owner, me}."""
    me = _me()
    if not (enabled() and room_id and owner and me and all(_matrix())):
        return None
    try:
        status, state = _mx("GET", f"rooms/{_q(room_id)}/state")
    except httpx.HTTPError as exc:
        logger.warning("phone tools: room state unreachable: %s", exc)
        return None
    if status != 200 or not isinstance(state, list):
        return None
    joined: set[str] = set()
    best: tuple[float, Target] | None = None
    now = _now()
    for ev in state:
        etype, content = ev.get("type"), ev.get("content") or {}
        if etype == "m.room.encryption":
            return None
        if etype == "m.room.member" and content.get("membership") == "join":
            joined.add(str(ev.get("state_key")))
        if etype != STATE_TYPE or ev.get("sender") != owner:
            continue
        device = str(ev.get("state_key") or "")
        expires = _parse_ts(content.get("expires_at"))
        tools = frozenset(t for t in content.get("tools") or [] if t in TOOL_NAMES.values())
        if not device or content.get("device_id") != device or expires is None or not tools:
            continue
        if not (now < expires <= now + MAX_BELIEVED_TTL_S):
            continue
        if best is None or expires > best[0]:
            best = (expires, Target(room_id, owner, device, tools))
    if joined != {owner, me} or best is None:
        return None
    return best[1]


# ── Per-turn binding: the turn's live phone (the model sees phone tools only then) ───────────────

_session_targets: dict[str, Target] = {}
_thread = threading.local()
_lock = threading.Lock()


def mark_turn(session_id: str, target: Target | None) -> None:
    with _lock:
        if target is None:
            _session_targets.pop(session_id, None)
        else:
            _session_targets[session_id] = target


def clear_turn(session_id: str) -> None:
    mark_turn(session_id, None)


def filter_tools(schemas: list[dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
    """Drop phone tools unless this turn has a live phone offering them; bind the turn's thread to it."""
    with _lock:
        target = _session_targets.get(session_id)
    _thread.target = target
    keep = []
    for schema in schemas:
        name = (schema.get("function") or {}).get("name", "")
        if name in TOOL_NAMES and (target is None or TOOL_NAMES[name] not in target.tools):
            continue
        keep.append(schema)
    return keep


def _current_target() -> Target | None:
    target = getattr(_thread, "target", None)
    if target is not None:
        return target
    with _lock:  # the tool ran on another thread than the turn: only an unambiguous target is used
        live = list(_session_targets.values())
    return live[0] if len(live) == 1 else None


# ── Requests in flight ───────────────────────────────────────────────────────────────────────────

@dataclass
class Pending:
    request_id: str
    tool: str
    room_id: str
    owner: str
    event_id: str
    issued_at: float
    expires_at: float
    started_event: str | None = None
    timed_out_at: float | None = None
    extra: dict[str, Any] = dc_field(default_factory=dict)


_pending: dict[str, Pending] = {}


def pending() -> list[Pending]:
    with _lock:
        return list(_pending.values())


def _new_id() -> str:
    return "tr_" + secrets.token_hex(8)


def send_request(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Send one tool_request to the turn's phone. Returns facts for the model, never waits."""
    target = _current_target()
    if target is None or tool not in target.tools:
        return {"ok": False, "error": "no_phone_online"}
    issued = _now()
    request_id = _new_id()
    content = {"id": request_id, "device_id": target.device_id, "tool": tool, "args": args,
               "issued_at": _iso(issued), "expires_at": _iso(issued + REQUEST_START_S)}
    try:
        status, data = _mx("PUT", f"rooms/{_q(target.room_id)}/send/{REQUEST_TYPE}/{request_id}", content)
    except httpx.HTTPError as exc:
        logger.warning("phone tools: request not sent: %s", exc)
        return {"ok": False, "error": "chat_unreachable"}
    event_id = data.get("event_id") if isinstance(data, dict) else None
    if status != 200 or not event_id:
        return {"ok": False, "error": f"chat_refused_{status}"}
    with _lock:
        _pending[request_id] = Pending(request_id, tool, target.room_id, target.owner, str(event_id),
                                       issued, issued + REQUEST_START_S)
    logger.info("phone tools: sent %s %s", tool, request_id)
    return {"ok": True, "status": "asked_phone", "request_id": request_id,
            "note": "The phone's answer arrives later as a new message in this chat."}


def contacts_search(*, query: str, field: str = "phone", limit: int = 20) -> dict[str, Any]:
    return send_request("contacts.search", {"query": query, "field": field, "limit": int(limit)})


def sms_compose(*, recipients: list[dict[str, Any]], body: str, mode: str = "individual") -> dict[str, Any]:
    clean = [{k: str(r[k]) for k in ("name", "phone") if r.get(k)} for r in recipients]
    return send_request("sms.compose", {"recipients": clean, "body": body, "mode": mode})


# ── The poller's step (called from the Matrix bot's loop through asyncio.to_thread) ──────────────

def _relations(p: Pending) -> list[dict[str, Any]]:
    path = f"rooms/{_q(p.room_id)}/relations/{_q(p.event_id)}/m.reference"
    status, data = _mx("GET", path, version="v1")
    if status != 200 or not isinstance(data, dict):
        return []
    return [ev for ev in data.get("chunk") or [] if ev.get("sender") == p.owner]


def _redact(room_id: str, event_id: str | None) -> None:
    if not event_id:
        return
    try:
        _mx("PUT", f"rooms/{_q(room_id)}/redact/{_q(event_id)}/{secrets.token_hex(8)}", {"reason": "phone tools"})
    except httpx.HTTPError as exc:
        logger.warning("phone tools: redaction failed: %s", exc)


def _content(ev: dict[str, Any], key: str) -> dict[str, Any]:
    value = (ev.get("content") or {}).get(key)
    return value if isinstance(value, dict) else {}


def poll_once() -> list[tuple[str, str, str]]:
    """Check every request in flight. Returns (room_id, owner, follow-up turn text) for the bot to run."""
    out: list[tuple[str, str, str]] = []
    now = _now()
    for p in pending():
        try:
            events = _relations(p)
        except httpx.HTTPError as exc:
            logger.warning("phone tools: relations unreachable: %s", exc)
            continue
        result_ev = next((ev for ev in events if ev.get("type") == RESULT_TYPE
                          and _content(ev, RESULT_TYPE).get("id") == p.request_id), None)
        started_ev = next((ev for ev in events if ev.get("type") == STARTED_TYPE
                           and _content(ev, STARTED_TYPE).get("id") == p.request_id), None)
        if started_ev:
            p.started_event = str(started_ev.get("event_id"))

        if p.timed_out_at is not None:  # best effort: one late result after a timeout is redacted unread
            if result_ev:
                _redact(p.room_id, result_ev.get("event_id"))
            if result_ev or now - p.timed_out_at > LATE_RESULT_KEEP_S:
                with _lock:
                    _pending.pop(p.request_id, None)
            continue

        if result_ev:
            result = _content(result_ev, RESULT_TYPE)
            for eid in (result_ev.get("event_id"), p.started_event, p.event_id):
                _redact(p.room_id, eid)
            with _lock:
                _pending.pop(p.request_id, None)
            out.append((p.room_id, p.owner, _result_text(p, result)))
            continue

        never_started = p.started_event is None and now > p.expires_at + START_GRACE_S
        no_answer = now > p.issued_at + RESULT_WAIT_S
        if never_started or no_answer:
            _redact(p.room_id, p.started_event)
            _redact(p.room_id, p.event_id)
            p.timed_out_at = now
            out.append((p.room_id, p.owner, _timeout_text(p, never_started)))
    return out


def _result_text(p: Pending, result: dict[str, Any]) -> str:
    import json

    if result.get("ok") is True:
        body = json.dumps(result.get("result") or {}, ensure_ascii=False, separators=(",", ":"))
        return f"[Phone result for request {p.request_id} ({p.tool})] {body}"
    return f"[Phone result for request {p.request_id} ({p.tool})] error: {result.get('error') or 'unknown'}"


def _timeout_text(p: Pending, never_started: bool) -> str:
    if never_started:
        return (f"[Phone result for request {p.request_id} ({p.tool})] timeout: the phone did not pick it up; "
                "it will not run it now.")
    return f"[Phone result for request {p.request_id} ({p.tool})] timeout: no answer from the phone; outcome unknown."
