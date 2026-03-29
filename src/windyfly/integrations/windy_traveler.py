"""Windy Traveler integration — translation and language detection."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Subset of supported languages (full list from Windy Traveler)
SUPPORTED_LANGUAGES = [
    "en", "es", "fr", "de", "it", "pt", "ru", "zh", "ja", "ko",
    "ar", "hi", "bn", "pa", "te", "mr", "ta", "ur", "vi", "th",
    "tr", "pl", "nl", "sv", "da", "no", "fi", "el", "cs", "ro",
    "hu", "sk", "bg", "hr", "sr", "sl", "et", "lv", "lt", "uk",
    "he", "fa", "sw", "id", "ms", "tl", "af", "am", "az", "be",
]


@dataclass
class TranslationResult:
    """Result of a text translation."""

    success: bool = False
    translated_text: str = ""
    source_lang: str = ""
    target_lang: str = ""
    error: str = ""


async def translate_text(
    text: str,
    target_lang: str,
    source_lang: str = "auto",
    jwt: str = "",
) -> TranslationResult:
    """Translate text using Windy Traveler.

    Returns an error result if the Windy Pro API is unavailable.
    """
    api_url = os.environ.get("WINDY_API_URL", "")
    jwt = jwt or os.environ.get("WINDY_JWT", "")

    if not api_url or not jwt:
        return TranslationResult(error="Windy Pro API not configured")

    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{api_url}/api/v1/translate/text",
                json={
                    "text": text,
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                },
                headers={"Authorization": f"Bearer {jwt}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return TranslationResult(
                    success=True,
                    translated_text=data.get("translated_text", ""),
                    source_lang=data.get("source_lang", source_lang),
                    target_lang=target_lang,
                )
            return TranslationResult(error=f"API returned {resp.status_code}")
    except Exception as exc:
        return TranslationResult(error=str(exc))


def get_supported_languages() -> list[str]:
    """Return the list of supported language codes."""
    return list(SUPPORTED_LANGUAGES)
