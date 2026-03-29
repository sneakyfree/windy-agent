"""Push notification gateway client — FCM + APNs."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PushResult:
    """Result of a push notification send."""

    success: bool = False
    message_id: str = ""
    error: str = ""


async def send_push(
    device_token: str,
    title: str,
    body: str,
    data: dict | None = None,
) -> PushResult:
    """Send a push notification via the Windy push gateway.

    The push gateway (K6) lives in the windy-pro repo. This is the
    client that talks to it.
    """
    gateway_url = os.environ.get("WINDY_PUSH_URL", "")

    if not gateway_url:
        logger.debug("Push gateway not configured — skipping")
        return PushResult(error="Push gateway not configured")

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{gateway_url}/api/v1/push",
                json={
                    "device_token": device_token,
                    "title": title,
                    "body": body,
                    "data": data or {},
                },
            )
            if resp.status_code == 200:
                result = resp.json()
                return PushResult(
                    success=True,
                    message_id=result.get("message_id", ""),
                )
            return PushResult(error=f"Gateway returned {resp.status_code}")
    except Exception as exc:
        return PushResult(error=str(exc))
