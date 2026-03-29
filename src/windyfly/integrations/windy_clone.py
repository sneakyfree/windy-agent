"""Windy Clone integration — voice clone training status."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CloneStatus:
    """Voice clone training status."""

    is_available: bool = False
    training_progress: float = 0.0
    phoneme_coverage: float = 0.0
    hours_recorded: float = 0.0
    is_ready: bool = False
    error: str = ""


async def get_clone_status(jwt: str = "") -> CloneStatus:
    """Get voice clone training status from Windy Pro API.

    Returns a default 'not started' status if the API is unavailable.
    """
    api_url = os.environ.get("WINDY_API_URL", "")
    jwt = jwt or os.environ.get("WINDY_JWT", "")

    if not api_url or not jwt:
        return CloneStatus(error="Windy Pro API not configured")

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_url}/api/v1/clone/training-data",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return CloneStatus(
                    is_available=True,
                    training_progress=data.get("progress", 0.0),
                    phoneme_coverage=data.get("phoneme_coverage", 0.0),
                    hours_recorded=data.get("hours_recorded", 0.0),
                    is_ready=data.get("is_ready", False),
                )
            return CloneStatus(error=f"API returned {resp.status_code}")
    except Exception as exc:
        return CloneStatus(error=str(exc))
