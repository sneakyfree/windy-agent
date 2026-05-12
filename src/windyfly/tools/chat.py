"""Chat tools — let the LLM send a Matrix message to the user.

Matches Sprint 1's email-tool pattern: a thin sync wrapper that
returns ``unavailable`` when env isn't set, ``sent`` on success, and
``failed`` on transport / API errors. The LLM can interpret each
shape and explain it to the user.

Why Matrix-REST directly instead of the existing ``WindyFlyMatrixBot``?
The bot is a long-lived async daemon owning its own AsyncClient with
encryption keys — the right surface for *receiving* messages and
maintaining presence, but a heavy hammer for "send one message and
return." The tool registry is also pure-sync; an asyncio.run wrapper
around nio just to send one event is a lot of moving parts. The
Matrix REST API (``PUT /_matrix/client/v3/rooms/{id}/send/...``) is
sync-callable via httpx and uses the same access token the bot does.

E2E encryption note: messages sent via REST are NOT encrypted. For
the ballroom-demo tier and the agent's "I just emailed Bob, also
sending you a chat note" pattern, that's acceptable — these messages
are status updates, not secrets. v1 should add encrypted-room
detection and either route through the daemon or refuse encrypted
sends with a clear error.

Environment:
    MATRIX_HOMESERVER     — e.g. https://chat.windychat.ai
    MATRIX_BOT_USER       — e.g. @grant-fly:windyword.ai
    MATRIX_BOT_TOKEN      — Matrix access token
    MATRIX_DM_ROOM        — (optional) default room for the agent's
                            owner DM. If unset, ``to_room`` is required.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry
from windyfly.trust.gate import TrustDenied, require_trust

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0

# Trust gate action name — matches GATED_ACTIONS in trust/gate.py.
# Per ADR-019 (Windy Chat is the strategic kernel) and ADR-020 (HiFly
# chat treatment EPT-gated), the moat IS agent-credentialed VIP access.
# Every chat send by an agent goes through this gate when the agent
# has a passport. Trust check uses cached snapshot (5-min TTL via
# trust_cache table) so the per-message overhead is microseconds.
_TRUST_ACTION = "post_chat_message"


def _trust_gate_enabled() -> bool:
    """Trust gate runs only when the agent has a passport.

    Agents hatched through Eternitas always have ETERNITAS_PASSPORT
    populated (set by `windyfly.hatch_orchestrator` at step 1). Pre-passport
    boot (e.g. test rigs that exercise chat without the full hatch
    ceremony, or pre-hatch debug calls) skips the gate gracefully.
    """
    return bool(os.environ.get("ETERNITAS_PASSPORT", "").strip())


def _matrix_env() -> tuple[str, str, str]:
    """Read Matrix REST creds from env. Returns ('', '', '') when unset."""
    return (
        os.environ.get("MATRIX_HOMESERVER", "").rstrip("/"),
        os.environ.get("MATRIX_BOT_USER", ""),
        os.environ.get("MATRIX_BOT_TOKEN", ""),
    )


def _default_dm_room() -> str:
    """The agent's owner DM, populated at hatch by mail/chat provisioning."""
    return os.environ.get("MATRIX_DM_ROOM", "")


def send_chat_message(body: str, to_room: str | None = None) -> dict[str, Any]:
    """Send a Matrix chat message to a room.

    ``to_room`` defaults to ``MATRIX_DM_ROOM`` (the agent's owner DM).
    If neither is provided, returns ``{status: "failed", ...}`` so the
    LLM can re-prompt the user for the room.
    """
    homeserver, _bot_user, bot_token = _matrix_env()
    if not (homeserver and bot_token):
        return {
            "status": "unavailable",
            "error": (
                "Chat is not configured for this agent. "
                "MATRIX_HOMESERVER and MATRIX_BOT_TOKEN must be set "
                "(usually populated by chat provisioning during hatch)."
            ),
        }

    room = (to_room or _default_dm_room()).strip()
    if not room:
        return {
            "status": "failed",
            "error": (
                "No room specified and MATRIX_DM_ROOM env is unset. "
                "Pass to_room explicitly (e.g. '!abc123:windyword.ai')."
            ),
        }

    if not body or not body.strip():
        return {"status": "failed", "error": "Body is empty"}

    # Trust gate (ADR-019 + ADR-020): only when the agent has a passport.
    # Gate denies via Eternitas Integrity Index for critical-band agents
    # or actions disallowed by the agent's current snapshot. Cache-backed
    # so the typical-case overhead is microseconds.
    if _trust_gate_enabled():
        try:
            decision = asyncio.run(require_trust(_TRUST_ACTION))
            logger.debug(
                "Chat trust gate ALLOW: band=%s clearance=%s",
                decision.snapshot.band, decision.snapshot.clearance_level,
            )
        except TrustDenied as denied:
            return {
                "status": "denied",
                "reason": denied.reason,
                "band": denied.band,
                "action": _TRUST_ACTION,
                "error": str(denied),
            }
        except Exception as exc:
            # Fail-open with loud log if the trust check itself errors
            # (Eternitas outage, network blip, SQLite cache miss with
            # offline node, etc.). The moat's strict enforcement is at
            # the chat server's directory service, not here — this is a
            # client-side fast-path. Per ADR-019 chat depends on Eternitas
            # being highly available; transient outage shouldn't take down
            # every agent's chat send simultaneously.
            logger.warning(
                "Chat trust gate FAIL-OPEN (exception during check): %s",
                exc,
            )

    txn_id = uuid.uuid4().hex
    url = (
        f"{homeserver}/_matrix/client/v3/rooms/{room}/send/m.room.message/{txn_id}"
    )
    try:
        resp = httpx.put(
            url,
            params={"access_token": bot_token},
            json={"msgtype": "m.text", "body": body},
            timeout=_TIMEOUT,
        )
    except httpx.ConnectError as exc:
        return {
            "status": "failed",
            "error": f"Cannot reach Matrix homeserver at {homeserver}: {exc}",
        }
    except httpx.HTTPError as exc:
        return {"status": "failed", "error": f"Matrix transport error: {exc}"}

    if resp.status_code in (200, 201):
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return {
            "status": "sent",
            "event_id": data.get("event_id", ""),
            "room": room,
        }

    # Surface the Matrix error code/message verbatim — they're
    # already structured (errcode + error fields).
    try:
        err = resp.json()
        return {
            "status": "failed",
            "error": err.get("error", resp.text[:200]),
            "errcode": err.get("errcode", ""),
            "http_status": resp.status_code,
        }
    except ValueError:
        return {
            "status": "failed",
            "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
        }


def register_chat_tools(registry: ToolRegistry) -> None:
    """Register ``send_chat_message`` with the tool registry."""
    registry.register(
        name="send_chat_message",
        description=(
            "Send a chat message from the agent to a Matrix room. Use "
            "this when the user asks you to message someone in chat, or "
            "when you want to ping the user mid-task with a status update "
            "(e.g. 'sent the email — Bob's reply will land here when it "
            "comes in'). If to_room isn't specified, sends to the "
            "agent's owner DM by default. Returns {status: 'sent', "
            "event_id, room} on success, {status: 'unavailable', error} "
            "if chat isn't configured for this agent, or {status: "
            "'failed', error, errcode?} on Matrix API errors."
        ),
        parameters={
            "type": "object",
            "properties": {
                "body": {
                    "type": "string",
                    "description": "The message text to send.",
                },
                "to_room": {
                    "type": "string",
                    "description": (
                        "Matrix room ID (e.g. '!abc123:windyword.ai'). "
                        "Defaults to the agent's owner DM if omitted."
                    ),
                },
            },
            "required": ["body"],
        },
        fn=send_chat_message,
    )
