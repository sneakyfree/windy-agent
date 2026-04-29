"""Offline mode — fallback when LLM APIs are unreachable.

Checks connectivity, tries local Ollama if available,
otherwise queues messages for later processing.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue
    from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def is_online() -> bool:
    """Check if LLM APIs are reachable. Lenient by default.

    Pre-fix this hit api.openai.com once with a 3-second timeout
    and went OFFLINE on any failure. A transient blip (DNS, CDN
    hop, momentary packet loss) made grandma see the offline-mode
    reply even when the bot was fine — surfaced 2026-04-29 by the
    v12 demo dry-run.

    New behavior:
      1. Probe the actual provider in use (Anthropic / OpenAI),
         not always OpenAI
      2. Retry once on timeout — small extra latency, much more
         reliable
      3. DEFAULT TO ONLINE if both probes fail. The downstream LLM
         call itself has cooldown-circuit-breaker logic; surface
         the real error rather than short-circuit to offline-mode
         on a flaky DNS query.
    """
    import httpx

    # Prefer the provider whose key is set; fall back to OpenAI.
    candidates: list[str] = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        candidates.append("https://api.anthropic.com")
    if os.environ.get("OPENAI_API_KEY"):
        candidates.append("https://api.openai.com")
    if not candidates:
        # No keys at all — the LLM call will fail loudly with a
        # friendly message via the error classifier. Treat as online
        # so we don't double-fall-back to offline.
        return True

    for url in candidates:
        for attempt in (1, 2):
            try:
                resp = httpx.get(url, timeout=3)
                if 200 <= resp.status_code < 500:
                    return True
            except Exception as e:
                logger.debug(
                    "is_online probe %s attempt %d failed: %s", url, attempt, e,
                )
                continue

    # Both probes failed both attempts. Could be a real outage OR a
    # flaky local network. Default to ONLINE so the call attempt
    # surfaces a useful error if it really is broken — rather than
    # silently dropping the user into offline-mode.
    logger.warning(
        "is_online probes failed; defaulting to ONLINE so the call attempt "
        "produces a real error if the upstream is genuinely down",
    )
    return True


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

_QUEUE_PATH = Path(os.environ.get(
    "WINDYFLY_OFFLINE_QUEUE",
    "data/offline_queue.json",
))


def queue_message(user_message: str, session_id: str = "") -> None:
    """Queue a message for processing when connectivity returns.

    Uses atomic write (temp file + rename) to prevent corruption.

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
    # Atomic write: write to temp file then rename
    tmp = _QUEUE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(queue, indent=2))
    tmp.rename(_QUEUE_PATH)
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


def replay_queued_messages(
    config: dict,
    db: "Database",
    write_queue: "WriteQueue",
    tool_registry: "ToolRegistry | None" = None,
) -> int:
    """Replay all queued messages now that we're back online.

    Args:
        config: Config dict.
        db: Database instance.
        write_queue: WriteQueue.
        tool_registry: Optional tool registry.

    Returns:
        Number of messages successfully replayed.
    """
    queue = get_queued_messages()
    if not queue:
        return 0

    from windyfly.agent.loop import agent_respond

    replayed = 0
    for msg in queue:
        try:
            agent_respond(
                config, db, write_queue,
                msg["message"], msg.get("session_id", "offline"),
                tool_registry,
            )
            replayed += 1
        except Exception as e:
            logger.error("Failed to replay queued message: %s", e)

    clear_queue()
    logger.info("Replayed %d/%d queued messages", replayed, len(queue))
    return replayed


def _load_queue() -> list[dict[str, str]]:
    """Load the queue from disk."""
    if _QUEUE_PATH.exists():
        try:
            return json.loads(_QUEUE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []


