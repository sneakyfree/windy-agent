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

Mirrors mail_provision.py (windy-mail #62): same EPT bearer pattern,
same never-crash contract.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)


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
    if not ept:
        logger.info(
            "No ETERNITAS_PASSPORT_TOKEN — skipping EPT chat session "
            "(legacy MATRIX_BOT_TOKEN/PASSWORD path will be used if set)"
        )
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{chat_url.rstrip('/')}/api/v1/onboarding/agent/session",
                headers={"Authorization": f"Bearer {ept}"},
            )
            if resp.status_code == 404:
                logger.warning(
                    "Chat says this agent was never provisioned — hatch "
                    "first (or re-run windy hatch)"
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
