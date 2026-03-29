"""Contact discovery — Signal-style hash matching."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredContact:
    """A contact found via hash matching."""

    phone_hash: str
    windy_user_id: str = ""
    display_name: str = ""
    has_agent: bool = False


def hash_phone(phone: str) -> str:
    """Hash a phone number for privacy-preserving discovery."""
    normalized = phone.strip().replace(" ", "").replace("-", "")
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


async def discover_contacts(
    phone_hashes: list[str],
) -> list[DiscoveredContact]:
    """Discover which phone numbers are on the Windy network.

    The contact discovery service (K3) lives in the windy-pro repo.
    This is the client that talks to it.
    """
    discovery_url = os.environ.get("WINDY_DISCOVERY_URL", "")

    if not discovery_url:
        logger.debug("Contact discovery not configured — returning empty")
        return []

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{discovery_url}/api/v1/discover",
                json={"hashes": phone_hashes},
            )
            if resp.status_code == 200:
                data = resp.json()
                return [
                    DiscoveredContact(
                        phone_hash=c.get("hash", ""),
                        windy_user_id=c.get("user_id", ""),
                        display_name=c.get("display_name", ""),
                        has_agent=c.get("has_agent", False),
                    )
                    for c in data.get("contacts", [])
                ]
    except Exception as exc:
        logger.warning("Contact discovery failed: %s", exc)

    return []
