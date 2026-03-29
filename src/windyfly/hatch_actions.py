"""Discrete actions performed during the Windy Fly hatch ceremony.

Each function is a single hatch step that can be called by the
hatch orchestrator. All actions are non-blocking — failures are
logged but never prevent the hatch from completing.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# SMS template for the first message sent by a newly hatched agent
HATCH_SMS_TEMPLATE = (
    "Hi! I'm {agent_name}, your new Windy Fly agent. "
    "I was just born and I'm ready to help! "
    "Download Windy Chat to talk to me: {chat_download_url}"
)

DEFAULT_CHAT_DOWNLOAD_URL = "https://windychat.com/download"


def format_hatch_sms(
    agent_name: str,
    chat_download_url: str = DEFAULT_CHAT_DOWNLOAD_URL,
) -> str:
    """Format the first SMS message from a newly hatched agent."""
    return HATCH_SMS_TEMPLATE.format(
        agent_name=agent_name,
        chat_download_url=chat_download_url,
    )


async def send_hatch_sms(
    owner_phone: str,
    agent_name: str,
    sms_channel=None,
    chat_download_url: str = DEFAULT_CHAT_DOWNLOAD_URL,
) -> dict:
    """Send the first SMS from the agent to its owner.

    Args:
        owner_phone: Owner's phone number.
        agent_name: The agent's name.
        sms_channel: A WindyFlySMS instance (or None for mock mode).
        chat_download_url: URL for Windy Chat download.

    Returns:
        Dict with status and message details.
    """
    message = format_hatch_sms(agent_name, chat_download_url)

    if sms_channel is None:
        # Mock mode — just log it
        logger.info(
            "Mock SMS-on-hatch: would send to %s: %s",
            owner_phone,
            message,
        )
        return {
            "status": "mock_sent",
            "to": owner_phone,
            "message": message,
        }

    try:
        result = sms_channel.send_sms(owner_phone, message)
        logger.info("Hatch SMS sent to %s: %s", owner_phone, result.get("status"))
        return result
    except Exception as exc:
        logger.warning("Hatch SMS failed: %s", exc)
        return {"status": "failed", "error": str(exc)}
