"""Agent teams v1 (windyfly side): list_my_agents + message_agent, same names and shapes as the Chat roster.

Contract: windy-contracts ``schema/chat/team-tools.v1.json`` (+ 1.1.0 x-http). Chat owns the routes:

    GET  {chat}/api/v1/onboarding/agent/my-agents  -> {ok, agents:[{name, passport, matrix_id, about?, self?}]}
    POST {chat}/api/v1/onboarding/agent/pair-room {to} -> {ok, room_id, created} | {ok:false, error, detail?}

Auth is the agent's own EPT+agent for aud ``windy-chat`` PLUS a DPoP proof on EVERY request, GET included
(htm = method, htu = the exact URL without query). The pair room is created AS THE OWNER with both agents
INVITED, so this agent joins it (idempotent) and then posts one ordinary m.text as itself. No wait tool: the
reply is the other agent's next message. Same-owner only (v1); Chat decides, this module never invents owners.
The same list also tells ``resolve_band`` which senders are SIBLINGS (TRUSTED band, Boss ruling 10-07).
Dark: WINDY_TEAMS=1.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

AUD = "windy-chat"
DEFAULT_BASE = "https://chat.windychat.ai"
_TIMEOUT_S = 10.0
SIBLING_TTL_S = 300.0

_lock = threading.Lock()
_siblings: dict[str, Any] = {"ids": frozenset(), "at": 0.0}


def _base() -> str:
    return (os.environ.get("WINDY_CHAT_API_URL") or DEFAULT_BASE).strip().rstrip("/")


def _unavailable(detail: str) -> dict[str, Any]:
    return {"ok": False, "error": "unavailable", "detail": detail[:200]}


def _call(method: str, route: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """One signed request to Chat. Raises RuntimeError(plain sentence) when we cannot even ask."""
    from windyfly.eternitas import agent_keys as ak

    url = f"{_base()}/api/v1/onboarding/agent/{route}"
    try:
        token = ak.request_agent_token(AUD)["token"]
        headers = {"Authorization": f"Bearer {token}", "DPoP": ak.service_dpop(method, url)}
        resp = httpx.request(method, url, json=body, headers=headers, timeout=_TIMEOUT_S)
    except httpx.HTTPError as exc:
        logger.info("teams: chat unreachable (%s)", type(exc).__name__)
        raise RuntimeError("I couldn't reach Windy Chat just now.") from exc
    except Exception as exc:  # noqa: BLE001  (AgentTokenError, key IO)
        logger.info("teams: no token for %s (%s)", AUD, getattr(exc, "code", type(exc).__name__))
        raise RuntimeError("I couldn't sign in to Windy Chat just now.") from exc
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data if isinstance(data, dict) else {}


def _remember(agents: list[dict[str, Any]]) -> None:
    ids = frozenset(str(a["matrix_id"]) for a in agents if a.get("matrix_id") and not a.get("self"))
    with _lock:
        _siblings["ids"], _siblings["at"] = ids, time.time()


def list_my_agents() -> dict[str, Any]:
    try:
        status, data = _call("GET", "my-agents")
    except RuntimeError as exc:
        return _unavailable(str(exc))
    if status == 200 and data.get("ok") is True:
        agents = [a for a in (data.get("agents") or []) if isinstance(a, dict)]
        _remember(agents)
        return {"ok": True, "agents": agents}
    if status == 404:
        return {"ok": True, "agents": []}  # Chat has no owner link for me yet
    return _unavailable("Windy Chat did not answer the list.")


def _join(room_id: str) -> str | None:
    """Accept the owner's invite (idempotent). None on success, else a plain reason."""
    homeserver = os.environ.get("MATRIX_HOMESERVER", "").rstrip("/")
    token = os.environ.get("MATRIX_BOT_TOKEN", "")
    if not (homeserver and token):
        return "chat is not configured for this agent"
    try:
        resp = httpx.post(f"{homeserver}/_matrix/client/v3/rooms/{room_id}/join",
                          headers={"Authorization": f"Bearer {token}"}, json={}, timeout=_TIMEOUT_S)
    except httpx.HTTPError:
        return "I couldn't reach the chat server"
    return None if resp.status_code == 200 else f"I could not join the room (HTTP {resp.status_code})"


def message_agent(to: str, text: str) -> dict[str, Any]:
    to, text = (to or "").strip(), (text or "").strip()
    if not to or not text:
        return {"ok": False, "error": "unknown_agent", "detail": "Say who to message and what to say."}
    if len(text) > 4000:
        return {"ok": False, "error": "unavailable", "detail": "That message is too long (4000 characters at most)."}
    try:
        status, data = _call("POST", "pair-room", {"to": to})
    except RuntimeError as exc:
        return _unavailable(str(exc))
    if not (status == 200 and data.get("ok") is True and data.get("room_id")):
        err = str(data.get("error") or "")
        if err in ("unknown_agent", "ambiguous", "not_your_agent", "self"):
            out: dict[str, Any] = {"ok": False, "error": err}
            if data.get("detail"):
                out["detail"] = str(data["detail"])[:200]
            return out
        return _unavailable("Windy Chat could not open the room.")
    room_id = str(data["room_id"])
    problem = _join(room_id)
    if problem:
        return _unavailable(problem)
    from windyfly.tools.chat import send_chat_message

    sent = send_chat_message(text, to_room=room_id)
    if sent.get("status") == "sent":
        return {"ok": True, "to": to}
    return _unavailable(str(sent.get("error") or "the message was not sent"))


# ── siblings (read by resolve_band; refreshed off the event loop) ────────────────────────

def sibling_ids() -> frozenset[str]:
    with _lock:
        return _siblings["ids"]


def siblings_stale() -> bool:
    with _lock:
        return time.time() - float(_siblings["at"]) > SIBLING_TTL_S


def refresh_siblings() -> None:
    """Blocking refresh; callers run it in a thread. A failure keeps the last good list."""
    try:
        list_my_agents()
    except Exception as exc:  # noqa: BLE001
        logger.debug("teams: sibling refresh failed: %s", exc)


def _reset_for_tests() -> None:
    with _lock:
        _siblings["ids"], _siblings["at"] = frozenset(), 0.0
