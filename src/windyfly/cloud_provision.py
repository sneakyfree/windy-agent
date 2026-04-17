"""Allocate a Windy Cloud storage quota for a newly hatched agent.

POST {WINDY_CLOUD_URL}/api/v1/billing/allocate
    Body: {windy_identity_id, passport_number, tier}
    Returns: {plan_id, quota_bytes, tier, expires_at?}

Called during hatch so every agent is born with a cloud home, not
just the ones whose owners remember to sign up for one.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class CloudAllocation:
    plan_id: str
    quota_bytes: int
    tier: str = "free"


async def allocate_cloud_quota(
    windy_identity_id: str,
    passport_number: str,
    tier: str = "free",
    cloud_url: str = "",
    bot_key: str = "",
    timeout: float = 10.0,
) -> CloudAllocation | None:
    """Ask Windy Cloud to provision a quota for this agent.

    Returns the allocation on success, None on skip/failure. Mirrors
    mail/matrix provisioning: never raises, never blocks hatch.
    """
    url = (cloud_url or os.environ.get("WINDY_CLOUD_URL", "")).rstrip("/")
    if not url:
        logger.info("Cloud quota skipped: WINDY_CLOUD_URL not set")
        return None
    if not passport_number:
        logger.info("Cloud quota skipped: no passport_number")
        return None

    auth = bot_key or os.environ.get("WINDY_CLOUD_TOKEN", "") or os.environ.get("WINDY_JWT", "")
    headers = {"Authorization": f"Bearer {auth}"} if auth else {}

    payload = {
        "windy_identity_id": windy_identity_id,
        "passport_number": passport_number,
        "tier": tier,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{url}/api/v1/billing/allocate",
                json=payload,
                headers=headers,
            )
        if resp.status_code not in (200, 201):
            logger.warning("Cloud quota allocate returned %s: %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        return CloudAllocation(
            plan_id=data.get("plan_id", ""),
            quota_bytes=int(data.get("quota_bytes", 0)),
            tier=data.get("tier", tier),
        )
    except httpx.RequestError as exc:
        logger.warning("Cloud quota allocate failed: %s", exc)
        return None
