"""make_call: phone the agent's OWNER, and only the owner, with a short message (Windy Call v1).

POST {WINDY_TEXT_BASE_URL}/call/v1/owner-call {"mode": "announce", "message": ...} on windy-text. The server picks
the destination (the owner's verified phone) and the caller id (the agent's own number); there is no ``to``,
no voice choice and no retry (a call costs money). The spoken audio comes from Windy Mind on the server side.

Auth is the one send_sms uses (tools/sms.py ``_send_auth``): a short EPT+agent for aud windy-telephony plus a
DPoP proof for this exact URL; a refused passport is never retried with another credential.

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
_TIMEOUT = 20.0
_DEFAULT_BASE_URL = "https://api.windytext.com"
_PATH = "/call/v1/owner-call"
MAX_MESSAGE = 500


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").strip() == "1"


def make_call(message: str) -> dict[str, Any]:
    """Phone the owner. Returns the server's facts: {status: 'calling', call_id, call_status, time_limit_seconds}
    or {status: 'refused', http_status, code} or {status: 'failed', error}."""
    from windyfly.tools.sms import _send_auth

    text = (message or "").strip()
    if not 1 <= len(text) <= MAX_MESSAGE:
        return {"status": "failed", "error": f"message must be 1-{MAX_MESSAGE} characters"}
    base = os.environ.get("WINDY_TEXT_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    url = f"{base}{_PATH}"
    passport = os.environ.get("ETERNITAS_PASSPORT_TOKEN", "")
    legacy = {"Authorization": f"Bearer {passport}"} if passport else {}
    headers, refused = _send_auth(url, legacy)
    if refused is not None:
        return {"status": "refused", "code": "passport_not_allowed"}
    if "Authorization" not in headers:
        return {"status": "failed", "error": "no agent credential for windy-text"}
    try:
        resp = httpx.post(url, json={"mode": "announce", "message": text}, headers=headers, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.info("make_call: windy-text unreachable (%s)", type(exc).__name__)
        return {"status": "failed", "error": "windy-text unreachable"}
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
            "call_id, call_status, time_limit_seconds} or {status: 'refused', http_status, code}."
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
