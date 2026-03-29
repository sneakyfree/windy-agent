"""Windy Word integration — voice recording search."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Recording:
    """A voice recording from Windy Word."""

    id: str = ""
    title: str = ""
    duration_seconds: float = 0.0
    language: str = ""
    transcript: str = ""
    created_at: str = ""


async def search_recordings(
    query: str, jwt: str = "", limit: int = 10
) -> list[Recording]:
    """Search voice recordings in Windy Word.

    Returns empty list if the Windy Pro API is unavailable.
    """
    api_url = os.environ.get("WINDY_API_URL", "")
    jwt = jwt or os.environ.get("WINDY_JWT", "")

    if not api_url or not jwt:
        return []

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_url}/api/v1/recordings/list",
                params={"q": query, "limit": limit},
                headers={"Authorization": f"Bearer {jwt}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return [
                    Recording(
                        id=r.get("id", ""),
                        title=r.get("title", ""),
                        duration_seconds=r.get("duration_seconds", 0.0),
                        language=r.get("language", ""),
                        transcript=r.get("transcript", ""),
                        created_at=r.get("created_at", ""),
                    )
                    for r in data.get("recordings", [])
                ]
    except Exception as exc:
        logger.warning("Recording search failed: %s", exc)

    return []


async def get_recording(recording_id: str, jwt: str = "") -> Recording | None:
    """Get a specific recording by ID."""
    api_url = os.environ.get("WINDY_API_URL", "")
    jwt = jwt or os.environ.get("WINDY_JWT", "")

    if not api_url or not jwt:
        return None

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_url}/api/v1/recordings/{recording_id}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            if resp.status_code == 200:
                r = resp.json()
                return Recording(
                    id=r.get("id", ""),
                    title=r.get("title", ""),
                    duration_seconds=r.get("duration_seconds", 0.0),
                    language=r.get("language", ""),
                    transcript=r.get("transcript", ""),
                    created_at=r.get("created_at", ""),
                )
    except Exception as exc:
        logger.warning("Recording fetch failed: %s", exc)

    return None
