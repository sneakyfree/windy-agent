"""Windy Pro API tools for the agent.

Provides 4 tools that connect to the Windy Pro account-server API:
translation history, recordings, clone status, and text translation.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Default timeout for API calls
_TIMEOUT = 10.0


def _get_api_url() -> str:
    """Get the Windy Pro API base URL."""
    return os.environ.get("WINDY_API_URL", "http://localhost:8098")


def _get_auth_headers() -> dict[str, str]:
    """Get authorization headers for the Windy Pro API."""
    jwt = os.environ.get("WINDY_JWT", "")
    headers: dict[str, str] = {}
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"
    return headers


def get_translation_history(limit: int = 10) -> dict[str, Any]:
    """Get recent translation history from Windy Pro.

    Args:
        limit: Maximum number of entries to return.

    Returns:
        Dict with translation history data.
    """
    try:
        response = httpx.get(
            f"{_get_api_url()}/api/v1/user/history",
            headers=_get_auth_headers(),
            params={"limit": limit},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error("Failed to get translation history: %s", e)
        return {"error": str(e), "translations": []}


def get_recordings(limit: int = 10) -> dict[str, Any]:
    """Get recent voice recordings from Windy Pro.

    Args:
        limit: Maximum number of recordings to return.

    Returns:
        Dict with recordings data.
    """
    try:
        response = httpx.get(
            f"{_get_api_url()}/api/v1/recordings/list",
            headers=_get_auth_headers(),
            params={"limit": limit},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error("Failed to get recordings: %s", e)
        return {"error": str(e), "recordings": []}


def get_clone_status() -> dict[str, Any]:
    """Get voice clone training status from Windy Pro.

    Returns:
        Dict with clone readiness, phoneme coverage, hours recorded.
    """
    try:
        response = httpx.get(
            f"{_get_api_url()}/api/v1/clone/training-data",
            headers=_get_auth_headers(),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error("Failed to get clone status: %s", e)
        return {"error": str(e)}


def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
) -> dict[str, Any]:
    """Translate text using Windy Pro.

    Args:
        text: Text to translate.
        source_lang: Source language code.
        target_lang: Target language code.

    Returns:
        Dict with translated text.
    """
    try:
        response = httpx.post(
            f"{_get_api_url()}/api/v1/translate/text",
            headers=_get_auth_headers(),
            json={
                "text": text,
                "source_lang": source_lang,
                "target_lang": target_lang,
            },
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        logger.error("Failed to translate text: %s", e)
        return {"error": str(e)}


def register_windy_tools(registry: ToolRegistry) -> None:
    """Register all Windy Pro API tools with the tool registry.

    Args:
        registry: ToolRegistry instance to register tools with.
    """
    registry.register(
        name="get_translation_history",
        description=(
            "Get the user's recent translation history from Windy Pro. "
            "Returns a list of recent translations with source/target languages and text."
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of entries to return (default: 10)",
                },
            },
            "required": [],
        },
        fn=get_translation_history,
    )

    registry.register(
        name="get_recordings",
        description=(
            "Get the user's recent voice recordings from Windy Pro. "
            "Returns a list of recordings with timestamps and durations."
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of recordings to return (default: 10)",
                },
            },
            "required": [],
        },
        fn=get_recordings,
    )

    registry.register(
        name="get_clone_status",
        description=(
            "Get the user's voice clone training status from Windy Pro. "
            "Returns clone readiness, phoneme coverage percentage, and hours recorded."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=get_clone_status,
    )

    registry.register(
        name="translate_text",
        description=(
            "Translate text from one language to another using Windy Pro's translation engine."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to translate",
                },
                "source_lang": {
                    "type": "string",
                    "description": "Source language code (e.g., 'en', 'es', 'fr')",
                },
                "target_lang": {
                    "type": "string",
                    "description": "Target language code (e.g., 'en', 'es', 'fr')",
                },
            },
            "required": ["text", "source_lang", "target_lang"],
        },
        fn=translate_text,
    )
