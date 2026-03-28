"""Offline mode — fallback when LLM APIs are unreachable.

Checks connectivity, tries local Ollama if available,
otherwise queues messages for later processing.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def is_online() -> bool:
    """Check if LLM APIs are reachable.

    Returns:
        True if online, False if offline.
    """
    import httpx
    try:
        response = httpx.get("https://api.openai.com", timeout=3)
        return 200 <= response.status_code < 500
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        return False


def is_ollama_available() -> bool:
    """Check if Ollama is running locally.

    Returns:
        True if Ollama is responding on localhost:11434.
    """
    import httpx
    try:
        response = httpx.get("http://localhost:11434/api/tags", timeout=2)
        return response.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        return False


def get_offline_response(
    user_message: str,
    context: list[dict[str, str]] | None = None,
) -> str:
    """Generate a response while offline.

    Tries local Ollama first, otherwise returns a queue notice.

    Args:
        user_message: The user's message.
        context: Optional conversation context.

    Returns:
        Response text (from Ollama or a queue notice).
    """
    if is_ollama_available():
        return _call_ollama(user_message, context)

    return (
        "I'm currently offline and don't have a local model available. "
        "I'll process your message when connectivity returns. 🪰"
    )


def _call_ollama(
    user_message: str,
    context: list[dict[str, str]] | None = None,
) -> str:
    """Call local Ollama for offline response."""
    import httpx

    messages = []
    if context:
        messages.extend(context[-5:])  # Last 5 messages for context
    messages.append({"role": "user", "content": user_message})

    try:
        response = httpx.post(
            "http://localhost:11434/api/chat",
            json={
                "model": "llama3.2",  # Default local model
                "messages": messages,
                "stream": False,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("message", {}).get("content", "No response from local model.")
    except Exception as e:
        logger.error("Ollama call failed: %s", e)
        return f"Local model error: {e}. Message queued for online processing."


# ---------------------------------------------------------------------------
# Persistent offline message queue
# ---------------------------------------------------------------------------

import json
import os
from pathlib import Path

_QUEUE_PATH = Path(os.environ.get(
    "WINDYFLY_OFFLINE_QUEUE",
    "data/offline_queue.json",
))


def queue_message(user_message: str, session_id: str = "") -> None:
    """Queue a message for processing when connectivity returns.

    Args:
        user_message: The user's message.
        session_id: Session ID for continuity.
    """
    _QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    queue = _load_queue()
    queue.append({
        "message": user_message,
        "session_id": session_id,
        "queued_at": __import__("datetime").datetime.now().isoformat(),
    })
    _QUEUE_PATH.write_text(json.dumps(queue, indent=2))
    logger.info("Queued offline message (%d in queue)", len(queue))


def get_queued_messages() -> list[dict[str, str]]:
    """Return all queued messages."""
    return _load_queue()


def clear_queue() -> int:
    """Clear the queue and return how many were cleared."""
    count = len(_load_queue())
    if _QUEUE_PATH.exists():
        _QUEUE_PATH.unlink()
    return count


def _load_queue() -> list[dict[str, str]]:
    """Load the queue from disk."""
    if _QUEUE_PATH.exists():
        try:
            return json.loads(_QUEUE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []

