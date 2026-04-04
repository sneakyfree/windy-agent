"""Discrete actions performed during the Windy Fly hatch ceremony.

Each function is a single hatch step that can be called by the
hatch orchestrator. All actions are non-blocking — failures are
logged but never prevent the hatch from completing.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# SMS template for the birth announcement
HATCH_SMS_TEMPLATE = (
    "🪰 IT'S ALIVE! Your AI agent {agent_name} just hatched!\n\n"
    "Chat with {agent_name} now:\n"
    "{dashboard_url}\n\n"
    "— Powered by Windy Fly"
)

DEFAULT_DASHBOARD_URL = "https://windyword.ai/app/fly"


def format_hatch_sms(
    agent_name: str,
    dashboard_url: str = DEFAULT_DASHBOARD_URL,
) -> str:
    """Format the birth announcement SMS."""
    return HATCH_SMS_TEMPLATE.format(
        agent_name=agent_name,
        dashboard_url=dashboard_url,
    )


async def send_hatch_sms(
    owner_phone: str,
    agent_name: str,
    dashboard_url: str = DEFAULT_DASHBOARD_URL,
    **kwargs,
) -> dict:
    """Send the birth announcement SMS to the owner.

    Tries real Twilio first, falls back to mock if not configured.

    Args:
        owner_phone: Owner's phone number.
        agent_name: The agent's name.
        dashboard_url: URL to the agent chat dashboard.

    Returns:
        Dict with status and message details.
    """
    message = format_hatch_sms(agent_name, dashboard_url)

    # Try real Twilio
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_PHONE_NUMBER", "")

    if sid and token and from_number:
        try:
            from twilio.rest import Client

            client = Client(sid, token)
            msg = client.messages.create(
                body=message, from_=from_number, to=owner_phone,
            )
            logger.info("Hatch SMS sent to %s (SID: %s)", owner_phone, msg.sid)
            return {"status": "sent", "sid": msg.sid, "to": owner_phone}
        except ImportError:
            logger.warning("Twilio SDK not installed — falling back to mock")
        except Exception as exc:
            logger.warning("Twilio send failed: %s — falling back to mock", exc)

    # Mock fallback
    logger.info("Mock SMS-on-hatch: %s → %s", owner_phone, message[:80])
    return {"status": "mock_sent", "to": owner_phone, "message": message}
