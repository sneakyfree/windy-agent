"""The agent's door to the owner's Windy Calendar (Agentic Calendar K1, windyfly side).

Contract: windy-contracts ``schema/calendar/tools-invoke.v1.json`` + docs/CALENDAR_AGENTIC_CONTRACT.md.

    POST {base}/invoke  {"name": <tool>, "arguments": {...}}  ->  {"ok": true, "result": ...}
                                                                or {"ok": false, "error": <code>, ...}

Auth is the agent's own EPT+agent (aud ``windy-calendar``) with a DPoP proof (``service_auth``).
Calendar decides what the agent may do (band rules, daily cap, owner confirmation); this module
only speaks the wire and turns every refusal into one plain sentence. ``confirm_required`` is HTTP
200 with ``ok:false``: a next step that waits for the OWNER, never a failure and never retried.
Dark by default: nothing runs unless WINDY_CALENDAR=1. Booker names/emails/phones are returned to
the caller (owner-only tools) but never logged here.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from windyfly.agent import service_auth

logger = logging.getLogger(__name__)

ENV_FLAG = "WINDY_CALENDAR"
AUD = "windy-calendar"
DEFAULT_BASE = "https://windycalendar.com"
_TIMEOUT_S = 10.0

_UNAVAILABLE = {"ok": False, "say": "I couldn't reach Windy Calendar just now. Try again in a moment."}


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").strip().lower() in ("1", "true", "on", "yes")


def _base() -> str:
    return (os.environ.get("WINDY_CALENDAR_URL") or DEFAULT_BASE).strip().rstrip("/")


def _plain(status: int, body: dict[str, Any], *, write: bool) -> dict[str, Any]:
    err, reason = str(body.get("error") or ""), str(body.get("reason") or "")
    if err == "confirm_required":
        raw = body.get("confirmation")
        conf: dict[str, Any] = raw if isinstance(raw, dict) else {}
        return {"ok": False, "pending_owner": True, "confirmation_id": conf.get("id"),
                "say": "That needs my owner's OK. It is waiting in the Windy Inbox."}
    if err == "denied":
        if reason == "agent_blocked":
            say = "My owner has blocked me from their calendar."
        elif reason == "no_calendar":
            say = "There isn't a Windy Calendar set up for my owner yet."
        elif write:
            say = "I'm not trusted enough yet to change my owner's calendar."
        else:
            say = "I can't see my owner's calendar yet."
        return {"ok": False, "say": say}
    if err == "rate_limited":
        return {"ok": False, "say": "I've reached today's limit for changing my owner's calendar. Tomorrow I can again."}
    if err == "conflict":
        return {"ok": False, "pending_owner": True,
                "say": "That is already waiting for my owner's approval."}
    if err == "invalid_arguments":
        if reason == "impossible_time":
            return {"ok": False, "say": "That time doesn't work. Pick a real time in the future."}
        if reason.startswith("block_span_over_7_days"):
            return {"ok": False, "say": "I can only block up to seven days at a time."}
        return {"ok": False, "say": "Windy Calendar didn't understand that request."}
    if err == "unauthorized":
        return {"ok": False, "say": "I couldn't sign in to Windy Calendar just now."}
    if err in ("not_implemented", "unknown_tool") or status == 404:
        return {"ok": False, "say": "Windy Calendar can't do that yet."}
    return dict(_UNAVAILABLE)


def invoke(name: str, arguments: dict[str, Any] | None = None, *, write: bool = False) -> dict[str, Any]:
    """Call one Calendar tool. Returns ``{"ok": True, "result": ...}`` or ``{"ok": False, "say": ...}``."""
    if not enabled():
        return {"ok": False, "say": "Windy Calendar isn't turned on for me yet."}
    url = f"{_base()}/invoke"
    payload = {"name": name, "arguments": arguments or {}}
    resp: httpx.Response | None = None
    for attempt in (0, 1):
        try:
            headers = service_auth.agent_headers(AUD, "POST", url)
            resp = httpx.post(url, json=payload, headers=headers, timeout=_TIMEOUT_S)
        except service_auth.ServiceAuthError as exc:
            logger.info("windy calendar: no token for %s (%s)", AUD, exc.code)
            return {"ok": False, "say": "I couldn't sign in to Windy Calendar just now."}
        except httpx.HTTPError as exc:
            logger.info("windy calendar: unreachable (%s)", type(exc).__name__)
            return dict(_UNAVAILABLE)
        if resp.status_code == 401 and attempt == 0:
            try:
                reason = str(resp.json().get("reason") or "")
            except ValueError:
                reason = ""
            if reason in ("expired", "invalid"):
                service_auth.forget_token()
                continue
        break
    assert resp is not None
    try:
        body = resp.json()
    except ValueError:
        return dict(_UNAVAILABLE)
    if not isinstance(body, dict):
        return dict(_UNAVAILABLE)
    if resp.status_code == 200 and body.get("ok") is True:
        return {"ok": True, "result": body.get("result")}
    logger.info("windy calendar: %s refused: %s %s", name, body.get("error"), body.get("reason"))
    return _plain(resp.status_code, body, write=write)
