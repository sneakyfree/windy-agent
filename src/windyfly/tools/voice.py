"""make_call: phone the agent's OWNER, and only the owner, with a short message (Windy Call v1).

POST {WINDY_TEXT_BASE_URL}/call/v1/owner-call {"mode": "announce", "message": ...} on windy-text. The server picks
the destination (the owner's verified phone) and the caller id (the agent's own number); there is no ``to``,
no voice choice and no retry (a call costs money). The spoken audio comes from Windy Mind on the server side.

Auth is mode B only (tools/sms.py ``agent_auth``): a short EPT+agent for aud windy-telephony plus a DPoP proof
for this exact URL. When either cannot be made the call is refused locally with that code; the long passport
token is never sent (Hub on #609).

DARK: registered only when WINDY_OWNER_CALL=1. The old body (api.windycall.com/voice/call, any number, Twilio
<Say> voices) is gone: that service is frozen and its host no longer resolves.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

ENV_FLAG = "WINDY_OWNER_CALL"
# The server renders the Mind clips one after another before it dials (each up to 20 s on a cold cache), so a
# first call can take well over 20 s (Windy Call on #609).
_TIMEOUT = 90.0
_DEFAULT_BASE_URL = "https://api.windytext.com"
_PATH = "/call/v1/owner-call"
MAX_MESSAGE = 500
# After a call that MAY have rung (placed, unknown, or windy-text's "may have dialled" 502), make_call refuses a
# second call to the owner for this long, in code (Boss 11-01: words to the model are not a control). Shared by
# every process of this agent through a state file. A clean refusal before any dial never arms it.
RECENT_CALL_S = 10 * 60
RECENT_CALL_SAY = "A call may already have been placed; ask your owner before calling again."


def _guard_path() -> Any:
    from windyfly.platform import windy_state_dir

    return windy_state_dir() / "owner_call_guard.json"


def _recent_call_until() -> float:
    try:
        data = json.loads(_guard_path().read_text(encoding="utf-8"))
        return float(data.get("until", 0)) if isinstance(data, dict) else 0.0
    except (OSError, ValueError, TypeError):
        return 0.0


def _arm_recent_call_guard(now: float) -> None:
    try:
        path = _guard_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"until": now + RECENT_CALL_S}), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:  # a guard we can't write must not hide the call's real result
        logger.warning("make_call: could not record the recent-call guard (%s)", type(exc).__name__)


def _idempotency_key(text: str) -> str:
    """The SAME intended call gets the SAME key, so windy-text drops a repeat inside its window (Call 11-01): the
    agent's passport + the message. Never the message itself on the wire, only its hash."""
    parts = (os.environ.get("ETERNITAS_PASSPORT", ""), text)
    return "oc-" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:40]


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").strip() == "1"


def make_call(message: str) -> dict[str, Any]:
    """Phone the owner. Returns the server's facts: {status: 'calling', call_id, call_status, time_limit_seconds},
    {status: 'refused', http_status, code}, {status: 'unknown', error} (no answer after the request may have
    arrived) or {status: 'failed', error} (nothing was placed)."""
    from windyfly.tools.sms import agent_auth

    text = (message or "").strip()
    if not 1 <= len(text) <= MAX_MESSAGE:
        return {"status": "failed", "error": f"message must be 1-{MAX_MESSAGE} characters"}
    now = time.time()
    if _recent_call_until() > now:
        logger.info("make_call: refused locally, a call may already have been placed")
        return {"status": "refused", "code": "recent_call", "note": RECENT_CALL_SAY}
    base = os.environ.get("WINDY_TEXT_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    url = f"{base}{_PATH}"
    headers, code = agent_auth(url)
    if headers is None:
        return {"status": "refused", "code": code}
    headers = {**headers, "Idempotency-Key": _idempotency_key(text)}
    try:
        resp = httpx.post(url, json={"mode": "announce", "message": text}, headers=headers, timeout=_TIMEOUT)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        # Nothing reached windy-text: no call can have been placed.
        logger.info("make_call: windy-text unreachable (%s)", type(exc).__name__)
        return {"status": "failed", "error": "windy-text unreachable"}
    except httpx.HTTPError as exc:
        # The request may have arrived: the owner's phone may already be ringing. A second try is NOT safe: the
        # server's 409 call_in_progress only covers a call still active (Hub 11-01), so the guard below refuses one.
        logger.info("make_call: no answer from windy-text (%s)", type(exc).__name__)
        _arm_recent_call_guard(now)
        return {"status": "unknown",
                "error": f"no answer from windy-text in {int(_TIMEOUT)} s; a call may still be placed"}
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if resp.status_code == 200 or resp.status_code == 502:
        _arm_recent_call_guard(now)  # placed, or windy-text's "may have dialled" (502)
    if resp.status_code == 200:
        out = {"status": "calling", "call_id": body.get("call_id"), "call_status": body.get("status"),
               "time_limit_seconds": body.get("time_limit_seconds")}
        # windy-text #124: the same Idempotency-Key within its window answers the FIRST call's 200 again, marked
        # `Idempotent-Replay: true`, and dials nothing. Say so, so the model doesn't report a second call.
        if resp.headers.get("Idempotent-Replay", "").strip().lower() == "true":
            out["replayed"] = True
        return out
    code = body.get("error") or body.get("code") or body.get("detail")
    logger.info("make_call: refused %s %s", resp.status_code, code)
    return {"status": "refused", "http_status": resp.status_code, "code": code if isinstance(code, str) else None}


def register_voice_tools(registry: ToolRegistry) -> None:
    """Register ``make_call`` only when WINDY_OWNER_CALL=1."""
    if not enabled():
        return
    registry.register(
        name="make_call",
        description=(
            "Phones your owner, and only your owner, and speaks a short message. Returns {status: 'calling', "
            "call_id, call_status, time_limit_seconds}, {status: 'refused', http_status, code} or "
            "{status: 'unknown', error} when windy-text did not answer in time (a call may still be placed). "
            "If the status is unknown, do not call again: tell your owner a call may have been placed. "
            "replayed: true means this is the same call as before: nothing new was dialled."
        ),
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "What to say, 1-500 characters.",
                            "minLength": 1, "maxLength": MAX_MESSAGE},
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        fn=make_call,
    )
