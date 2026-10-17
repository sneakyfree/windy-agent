"""Answer in the sender's language (strand P2.11.13).

Windy apps tag each Matrix message with ``content.windy_lang``, the language the person writes in. Until now
only outgoing messages set it and nothing read it. Now the Matrix channel notes the tag from each incoming
message for that conversation, and the prompt asks the model to answer in that language.

The tag is untrusted input, so only a well-formed language code is kept (``es``, ``pt-BR``). The prompt line
names the language from a fixed table (the languages Windy serves) and otherwise uses the bare code. Text
from the event never reaches the prompt. A message without a tag leaves the last known language in place.
"""

from __future__ import annotations

import re
import threading

_CODE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?")

# The languages Windy serves (account server, GET /api/v1/translate/languages, 10-17).
NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ar": "Arabic", "ru": "Russian", "pl": "Polish",
    "hi": "Hindi", "tr": "Turkish", "vi": "Vietnamese", "th": "Thai", "nl": "Dutch", "sv": "Swedish",
    "uk": "Ukrainian", "id": "Indonesian",
}

_by_session: dict[str, str] = {}
_lock = threading.Lock()


def from_content(content: object) -> str | None:
    """The well-formed language code in an event's content, or None."""
    if not isinstance(content, dict):
        return None
    raw = content.get("windy_lang")
    if not isinstance(raw, str):
        return None
    code = raw.strip()
    if "-" in code:
        base, _, region = code.partition("-")
        code = f"{base.lower()}-{region}"
    else:
        code = code.lower()
    return code if _CODE.fullmatch(code) else None


def note(session_id: str, code: str | None) -> None:
    """Remember the sender's language for this conversation (a message without one changes nothing)."""
    if code:
        with _lock:
            _by_session[session_id] = code


def get(session_id: str) -> str | None:
    with _lock:
        return _by_session.get(session_id)


def prompt_line(session_id: str) -> str | None:
    """The system-prompt line for this conversation, or None (no tag known, or English)."""
    code = get(session_id)
    if not code:
        return None
    base = code.split("-", 1)[0]
    if base == "en":
        return None
    name = NAMES.get(base, f"the language with code {code}")
    return (f"LANGUAGE: the person writes in {name} ({code}), as their Windy app reports. Answer in {name} "
            "unless they ask for another language.")
