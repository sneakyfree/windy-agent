"""Agent teams v1 (windyfly side): list_my_agents + message_agent, same names and shapes as the Chat roster.

Contract: windy-contracts ``schema/chat/team-tools.v1.json`` (+ 1.1.0 x-http). Chat owns the routes:

    GET  {chat}/api/v1/onboarding/agent/my-agents  -> {ok, agents:[{name, passport, matrix_id, about?, self?}]}
    POST {chat}/api/v1/onboarding/agent/pair-room {to} -> {ok, room_id, created, partner_joined?}
                                                          | {create:true, claim, name, invite, owner_mxid} (1.3.0)
                                                          | {ok:false, error, detail?}
    POST {chat}/api/v1/onboarding/agent/pair-room/confirm {claim, room_id} -> {room_id, created, partner_joined}

Auth is the agent's own EPT+agent for aud ``windy-chat`` PLUS a DPoP proof on EVERY request, GET included
(htm = method, htu = the exact URL without query). The pair room is created AS THE OWNER with both agents
INVITED, so this agent joins it (idempotent) and then posts one ordinary m.text as itself. When Chat runs session-free
pair rooms it answers a claim instead, and THIS agent creates the room under its own token (no session on anyone's
account), confirms it, then posts. No wait tool: the
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


def _call(method: str, route: str, body: dict[str, Any] | None = None,
          params: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    """One signed request to Chat. Raises RuntimeError(plain sentence) when we cannot even ask.
    ``params`` go in the query string; the DPoP proof signs the URL without it (RFC 9449)."""
    from windyfly.eternitas import agent_keys as ak

    url = f"{_base()}/api/v1/onboarding/agent/{route}"
    try:
        token = ak.request_agent_token(AUD)["token"]
        headers = {"Authorization": f"Bearer {token}", "DPoP": ak.service_dpop(method, url)}
        resp = httpx.request(method, url, json=body, params=params, headers=headers, timeout=_TIMEOUT_S)
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


call = _call  # the invite gate (channels/invite_gate.py) asks Chat through the same signed door


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
    homeserver, token = _matrix()
    if not (homeserver and token):
        return "chat is not configured for this agent"
    try:
        resp = httpx.post(f"{homeserver}/_matrix/client/v3/rooms/{room_id}/join",
                          headers={"Authorization": f"Bearer {token}"}, json={}, timeout=_TIMEOUT_S)
    except httpx.HTTPError:
        return "I couldn't reach the chat server"
    return None if resp.status_code == 200 else f"I could not join the room (HTTP {resp.status_code})"


# Session-free pair rooms (Chat's TEAM_PAIR_SESSIONLESS, windy-contracts team-tools.v1 1.3.0): onboarding may answer a
# CLAIM instead of a room; then THIS agent creates the room under its own Matrix token and confirms it. A room made for a
# claim but not confirmed yet is kept here, so a retry confirms THAT room instead of making a second one.
_created_for_claim: dict[str, str] = {}

# Plain facts for the model when onboarding refuses a claim (contract error enum).
_CLAIM_REFUSALS = {
    "not_siblings_yet": "that agent isn't registered as one of your owner's agents yet",
    "agent_revoked": "that agent is no longer active",
    "claim_in_use": "a room with that agent is being set up right now",
}


def _matrix() -> tuple[str, str]:
    return os.environ.get("MATRIX_HOMESERVER", "").rstrip("/"), os.environ.get("MATRIX_BOT_TOKEN", "")


def _create_pair_room(answer: dict[str, Any]) -> tuple[str | None, str | None]:
    """createRoom AS THIS AGENT, exactly the contract recipe. -> (room_id, None) | (None, plain reason)."""
    homeserver, token = _matrix()
    if not (homeserver and token):
        return None, "chat is not configured for this agent"
    owner = str(answer.get("owner_mxid") or "")
    body: dict[str, Any] = {
        "name": str(answer.get("name") or ""),
        "preset": "private_chat",
        "invite": [str(i) for i in (answer.get("invite") or [])],
        "creation_content": {"ai.windy.pair_claim": str(answer["claim"])},
    }
    if owner:
        body["power_level_content_override"] = {"users": {owner: 100}}
    try:
        resp = httpx.post(f"{homeserver}/_matrix/client/v3/createRoom", headers={"Authorization": f"Bearer {token}"},
                          json=body, timeout=_TIMEOUT_S)
    except httpx.HTTPError:
        return None, "I couldn't reach the chat server"
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code == 200 and isinstance(data, dict) and data.get("room_id"):
        return str(data["room_id"]), None
    if resp.status_code == 429:
        wait = data.get("retry_after_ms") if isinstance(data, dict) else None
        return None, f"the chat server is rate-limiting new rooms (retry after {int(wait or 0) // 1000} s)"
    return None, f"I could not create the room (HTTP {resp.status_code})"


def _confirm(claim: str, room_id: str) -> dict[str, Any]:
    """-> {room_id, partner_joined} | {error, detail}."""
    try:
        status, data = _call("POST", "pair-room/confirm", {"claim": claim, "room_id": room_id})
    except RuntimeError as exc:
        return _unavailable(str(exc))
    if status == 200 and data.get("room_id"):
        _created_for_claim.pop(claim, None)
        return {"room_id": str(data["room_id"]), "partner_joined": data.get("partner_joined") is not False}
    if status >= 500 or status == 429:
        return _unavailable("Windy Chat could not check the new room; try again.")
    _created_for_claim.pop(claim, None)  # definitive: the next try gets a fresh claim
    reason = str(data.get("reason") or data.get("error") or status)[:60]
    return _unavailable(f"Windy Chat refused the new room ({reason}).")


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
    err = str(data.get("error") or "")
    if err in _CLAIM_REFUSALS:
        return _unavailable(_CLAIM_REFUSALS[err])
    if status == 200 and data.get("create") is True and data.get("claim"):
        claim = str(data["claim"])
        room_id = _created_for_claim.get(claim)
        if not room_id:
            room_id, problem = _create_pair_room(data)
            if problem or not room_id:
                return _unavailable(problem or "I could not create the room")
            _created_for_claim[claim] = room_id  # kept BEFORE confirm: a retry confirms this room, never a second one
        confirmed = _confirm(claim, room_id)
        if confirmed.get("error"):
            return confirmed
        room_id, partner_joined = confirmed["room_id"], confirmed["partner_joined"]
    elif status == 200 and data.get("ok") is True and data.get("room_id"):
        room_id, partner_joined = str(data["room_id"]), data.get("partner_joined") is not False
        problem = _join(room_id)
        if problem:
            return _unavailable(problem)
    else:
        if err in ("unknown_agent", "ambiguous", "not_your_agent", "self"):
            out: dict[str, Any] = {"ok": False, "error": err}
            if data.get("detail"):
                out["detail"] = str(data["detail"])[:200]
            return out
        return _unavailable("Windy Chat could not open the room.")
    from windyfly.tools.chat import send_chat_message

    sent = send_chat_message(text, to_room=room_id)
    if sent.get("status") != "sent":
        return _unavailable(str(sent.get("error") or "the message was not sent"))
    result: dict[str, Any] = {"ok": True, "to": to}
    if not partner_joined:
        result["note"] = f"{to} has not joined the room yet; your message is there for them"
    return result


def room_has_other_agent(room_id: str, me: str) -> bool:
    """True when another AGENT account is joined or invited in the room (a team room): the welcome
    line is not posted there. Blocking; callers run it in a thread. Unknown = False (welcome as before)."""
    from windyfly.channels import parity

    homeserver = os.environ.get("MATRIX_HOMESERVER", "").rstrip("/")
    token = os.environ.get("MATRIX_BOT_TOKEN", "")
    if not (homeserver and token):
        return False
    try:
        resp = httpx.get(f"{homeserver}/_matrix/client/v3/rooms/{room_id}/members",
                         headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT_S)
        if resp.status_code != 200:
            return False
        for ev in resp.json().get("chunk", []):
            uid = str(ev.get("state_key") or "")
            if uid != me and parity.passport_of(uid) and (ev.get("content") or {}).get("membership") in ("join", "invite"):
                return True
    except (httpx.HTTPError, ValueError):
        return False
    return False


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
    _created_for_claim.clear()
    with _lock:
        _siblings["ids"], _siblings["at"] = frozenset(), 0.0
