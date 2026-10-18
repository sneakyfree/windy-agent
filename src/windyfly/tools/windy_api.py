"""Windy Pro API tools for the agent.

Provides 4 tools that connect to the Windy Pro account-server API:
translation history, recordings, clone status, and text translation.
"""

from __future__ import annotations

import json
import logging
import os
import unicodedata
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Default timeout for API calls
_TIMEOUT = 10.0


def _get_api_url() -> str:
    """Get the Windy Pro API base URL."""
    return os.environ.get("WINDY_API_URL", "http://localhost:8098").rstrip("/")


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
    except httpx.ConnectError as e:
        logger.error("Windy Pro is not available: %s", e)
        return {"error": "Windy Pro is not available right now", "translations": []}
    except httpx.HTTPError as e:
        logger.error("Failed to get translation history: %s", e)
        return {"error": str(e), "translations": []}


def get_recordings(limit: int = 10, query: str = "") -> dict[str, Any]:
    """Get recent voice recordings from Windy Pro.

    Args:
        limit: Maximum number of recordings to return.
        query: Optional search query to filter recordings.

    Returns:
        Dict with recordings data. Returns empty list if no cloud-synced
        recordings are available (desktop recordings are local SQLite).
    """
    try:
        params: dict[str, Any] = {"limit": limit}
        if query:
            params["q"] = query
        response = httpx.get(
            f"{_get_api_url()}/api/v1/recordings/list",
            headers=_get_auth_headers(),
            params=params,
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("recordings"):
            return {"recordings": [], "message": "No cloud-synced recordings found. Desktop recordings are stored locally."}
        return data
    except httpx.ConnectError as e:
        logger.error("Windy Pro is not available: %s", e)
        return {"error": "Windy Pro is not available right now", "recordings": []}
    except httpx.HTTPError as e:
        logger.error("Failed to get recordings: %s", e)
        return {"error": str(e), "recordings": []}


def get_clone_status() -> dict[str, Any]:
    """Get voice clone training status from Windy Pro.

    Returns:
        Dict with clone readiness, phoneme coverage, hours recorded.
        Returns a friendly message if the clone service is not yet available.
    """
    try:
        response = httpx.get(
            f"{_get_api_url()}/api/v1/clone/training-data",
            headers=_get_auth_headers(),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError:
        logger.info("Clone service not available (connection error)")
        return {"message": "Clone service is not available right now", "available": False}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.info("Clone service endpoint not found — service may not be deployed yet")
            return {"message": "Clone service is not available yet", "available": False}
        logger.error("Failed to get clone status: %s", e)
        return {"error": str(e), "available": False}
    except httpx.HTTPError as e:
        logger.error("Failed to get clone status: %s", e)
        return {"message": "Clone service is not available right now", "available": False}


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
    except httpx.ConnectError as e:
        logger.error("Windy Pro is not available: %s", e)
        return {"error": "Windy Pro is not available right now"}
    except httpx.HTTPError as e:
        logger.error("Failed to translate text: %s", e)
        return {"error": str(e)}


# The language list is untrusted remote data that lands in a tool result (Hub on #600): bounded in bytes and
# count, every field checked or coerced.
LANG_MAX_BYTES = 256 * 1024
LANG_MAX = 200
LANG_NAME_MAX = 64
LANG_TIERS = ("great", "good", "basic", "unrated")   # account-server translate-languages.ts Tier; null for English


def _lang_client() -> httpx.Client:
    # No redirects (a 3xx is refused, the other host is never contacted) and no proxy from the environment.
    return httpx.Client(timeout=_TIMEOUT, follow_redirects=False, trust_env=False)


def _clean_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(ch for ch in value if not unicodedata.category(ch).startswith("C"))
    return " ".join(text.split())[:LANG_NAME_MAX]


def _tier(value: Any) -> str | None:
    return value if isinstance(value, str) and value in LANG_TIERS else None


def list_languages() -> dict[str, Any]:
    """The languages Windy translates, with honest quality tiers (strand P4.11.21).

    ``GET /api/v1/translate/languages`` is public on the account server, so no credential is sent. Each
    language carries its code, English and native name, and the measured tier to and from English
    (great | good | basic | unrated; never a score). Read fresh on every call (not cached). At most
    256 KB and 200 languages are read; a code that is not a well-formed language code is dropped, names are
    plain text of at most 64 characters, and a tier outside the four words is shown as unknown (null).
    """
    from windyfly.channels.sender_lang import from_content as _lang_code

    url = f"{_get_api_url()}/api/v1/translate/languages"
    try:
        # identity: iter_bytes would inflate a gzip body before the cap could see it (Hub: 100 KB -> 100 MB).
        with _lang_client() as client, client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
            if response.status_code != 200:
                logger.error("Failed to list languages: HTTP %s", response.status_code)
                return {"error": "Couldn't read the language list from Windy Pro"}
            if response.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
                logger.error("Failed to list languages: compressed answer refused")
                return {"error": "Couldn't read the language list from Windy Pro"}
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > LANG_MAX_BYTES:
                return {"error": "Windy Pro's language list is too large to read"}
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw += chunk
                if len(raw) > LANG_MAX_BYTES:
                    return {"error": "Windy Pro's language list is too large to read"}
        body = json.loads(bytes(raw))
    except httpx.ConnectError as e:
        logger.error("Windy Pro is not available: %s", e)
        return {"error": "Windy Pro is not available right now"}
    except (httpx.HTTPError, ValueError, RecursionError) as e:    # RecursionError: JSON nested too deep
        logger.error("Failed to list languages: %s", type(e).__name__)
        return {"error": "Couldn't read the language list from Windy Pro"}
    langs = body.get("languages") if isinstance(body, dict) else None
    out: list[dict[str, Any]] = []
    for lang in langs if isinstance(langs, list) else []:
        if len(out) >= LANG_MAX:
            break
        if not isinstance(lang, dict) or not isinstance(lang.get("code"), str):
            continue
        code = _lang_code({"windy_lang": lang["code"]})
        if code is None:
            continue
        out.append({"code": code, "name": _clean_name(lang.get("name")),
                    "native_name": _clean_name(lang.get("native_name")),
                    "from_en": _tier(lang.get("from_en")), "to_en": _tier(lang.get("to_en"))})
    version = body.get("version") if isinstance(body, dict) else None
    return {"languages": out, "count": len(out),
            "version": version if isinstance(version, int) and not isinstance(version, bool) else None,
            "truncated": isinstance(langs, list) and len(langs) > LANG_MAX,
            "tiers": "great, good and basic are measured; unrated means served but not yet measured"}


_PROBE_TIMEOUT = 3.0


def windy_pro_answers() -> bool:
    """True only when this agent has a Windy Pro credential AND Windy Pro answers it (one GET, 3 s).

    Same rule as every other integration since 10-10 (#490/#492): a tool is offered only when it can work.
    No credential, no answer, 401/403 or any error = the tools are not registered this run.
    """
    if not os.environ.get("WINDY_JWT", "").strip():
        return False
    try:
        resp = httpx.get(f"{_get_api_url()}/api/v1/user/history", params={"limit": 1},
                         headers=_get_auth_headers(), timeout=_PROBE_TIMEOUT)
    except httpx.HTTPError:
        return False
    return resp.status_code == 200


def register_windy_tools(registry: ToolRegistry) -> None:
    """Register the Windy Pro API tools, only when Windy Pro answers this agent's credential.

    Args:
        registry: ToolRegistry instance to register tools with.
    """
    if not windy_pro_answers():
        logger.info(
            "windy pro: tools not registered (no WINDY_JWT, or Windy Pro did not answer it at start); "
            "this is checked once at start, so restart the agent to retry"
        )
        return
    registry.register(
        name="list_languages",
        description=(
            "The languages Windy can translate, each with its code, name and quality to and from English "
            "(great / good / basic / unrated). Use for 'what languages can you translate?' or before choosing "
            "a language code."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=list_languages,
    )
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
            "Returns a list of recordings with timestamps and durations. "
            "Note: desktop recordings are local — only cloud-synced recordings appear here."
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of recordings to return (default: 10)",
                },
                "query": {
                    "type": "string",
                    "description": "Optional search query to filter recordings",
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
            "Returns clone readiness, phoneme coverage percentage, and hours recorded. "
            "May return 'not available' if the clone service is not yet deployed."
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
