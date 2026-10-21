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

import logging
import os
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
    base = os.environ.get("WINDY_TEXT_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    url = f"{base}{_PATH}"
    headers, code = agent_auth(url)
    if headers is None:
        return {"status": "refused", "code": code}
    try:
        resp = httpx.post(url, json={"mode": "announce", "message": text}, headers=headers, timeout=_TIMEOUT)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        # Nothing reached windy-text: no call can have been placed.
        logger.info("make_call: windy-text unreachable (%s)", type(exc).__name__)
        return {"status": "failed", "error": "windy-text unreachable"}
    except httpx.HTTPError as exc:
        # The request may have arrived: the owner's phone may already be ringing. A second try is safe (the
        # server answers 409 call_in_progress while a call is active).
        logger.info("make_call: no answer from windy-text (%s)", type(exc).__name__)
        return {"status": "unknown",
                "error": f"no answer from windy-text in {int(_TIMEOUT)} s; a call may still be placed"}
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if resp.status_code == 200:
        return {"status": "calling", "call_id": body.get("call_id"), "call_status": body.get("status"),
                "time_limit_seconds": body.get("time_limit_seconds")}
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
            "{status: 'unknown', error} when windy-text did not answer in time (a call may still be placed)."
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
