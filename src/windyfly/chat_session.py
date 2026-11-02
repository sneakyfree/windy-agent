"""Claim the agent's own chat identity — the one-soul handoff.

The hatch provisions ``@agent_<passport>`` on Windy Chat, but until
2026-07-05 the real Windy Fly had no way to LOG IN as it: the old
matrix_provision path needed a Synapse registration secret (never on
user machines) and a fixed ``windyfly`` localpart that collided on the
second hatch. This module replaces that dead path.

The agent presents its own Eternitas Passport Token to
``POST /api/v1/onboarding/agent/session`` (windy-chat #111) and gets a
fresh Matrix device session for its provisioned identity. While the
Fly holds the ``matrix`` runtime-claim slot (main.py wires this), the
chat-side midwife (agent-roster) yields — one soul, one voice.

Uses the same EPT bearer pattern as the (since removed) CLI mail
provisioning (windy-mail #62), with the same never-crash contract.

Mode B (dark: WINDY_CHAT_EPT_AGENT=1, Chat 2026-11-01): an EPT+agent for aud "windy-chat" plus a DPoP proof for
this exact URL (the shared service_auth helper). A refused one (401/403) or a failed mint falls back to the
365-day EPT until 2026-12-15, when every legacy fallback goes (windy-agent's EPT plan).
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

ENV_FLAG = "WINDY_CHAT_EPT_AGENT"
CHAT_AUD = "windy-chat"


def mode_b_enabled() -> bool:
    return os.environ.get(ENV_FLAG, "") == "1"


def _agent_headers(url: str) -> dict[str, str] | None:
    """EPT+agent + DPoP for this POST, or None when the mint failed (the caller falls back to the legacy EPT)."""
    from windyfly.agent import service_auth

    try:
        return service_auth.agent_headers(CHAT_AUD, "POST", url)
    except service_auth.ServiceAuthError as exc:
        logger.warning("Chat session: no EPT+agent (%s %s); using the legacy EPT", exc.code, exc.detail)
        return None


async def fetch_agent_chat_session(config: dict | None = None) -> dict | None:
    """Mint this agent's Matrix session from its EPT.

    Returns ``{matrix_user_id, access_token, device_id, home_server,
    dm_room_id}`` on success, ``None`` when the agent has no passport
    token or the session can't be minted (callers fall back to the
    legacy MATRIX_BOT_TOKEN/PASSWORD envs — never crash the channel).
    """
    chat_url = ""
    if config:
        chat_url = config.get("ecosystem", {}).get("windy_chat_url", "")
    if not chat_url:
        chat_url = os.environ.get("WINDYCHAT_API_URL", "https://chat.windychat.ai")

    ept = os.environ.get("ETERNITAS_PASSPORT_TOKEN")
    if not ept and not mode_b_enabled():
        logger.info(
            "No ETERNITAS_PASSPORT_TOKEN — skipping EPT chat session "
            "(legacy MATRIX_BOT_TOKEN/PASSWORD path will be used if set)"
        )
        return None

    url = f"{chat_url.rstrip('/')}/api/v1/onboarding/agent/session"
    agent = _agent_headers(url) if mode_b_enabled() else None
    if agent is None and not ept:
        return None  # mode B couldn't mint and there is no legacy EPT to fall back on
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=agent or {"Authorization": f"Bearer {ept}"})
            if agent is not None and resp.status_code in (401, 403) and ept:
                logger.info("[chat-auth] Chat answered %d to the EPT+agent; retried with the old one",
                            resp.status_code)
                resp = await client.post(url, headers={"Authorization": f"Bearer {ept}"})
            if resp.status_code == 404:
                logger.warning(
                    "Chat says this agent was never provisioned — hatch "
                    "first (run `windy go`)"
                )
                return None
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "Chat session minted for %s (device %s)",
                data.get("matrix_user_id"),
                data.get("device_id"),
            )
            return data
    except httpx.ConnectError:
        logger.warning("Cannot reach Windy Chat at %s — session skipped", chat_url)
        return None
    except Exception as exc:  # never crash the channel over chat session
        logger.warning("EPT chat session failed: %s", exc)
        return None
