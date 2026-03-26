"""ChatGPT data export parser for Soul Continuity.

Parses a ChatGPT data export (conversations.json) and extracts
user preferences, topics, and communication style.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def parse_chatgpt(export_path: str) -> dict[str, Any]:
    """Parse a ChatGPT data export.

    Reads conversations.json and extracts user preferences, topics discussed,
    and communication style patterns.

    Args:
        export_path: Path to the ChatGPT export directory.

    Returns:
        Standardized import dict (no skills — ChatGPT doesn't export them).
    """
    path = Path(export_path)
    result: dict[str, Any] = {
        "personality": {},
        "memories": [],
        "skills": [],  # ChatGPT doesn't export skills
        "source": "chatgpt",
    }

    # Parse conversations.json
    conv_file = path / "conversations.json"
    if not conv_file.exists():
        logger.warning("No conversations.json found in %s", export_path)
        return result

    try:
        conversations = json.loads(conv_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, Exception) as e:
        logger.error("Failed to parse conversations.json: %s", e)
        return result

    if not isinstance(conversations, list):
        logger.warning("conversations.json is not a list")
        return result

    # Extract topics and preferences from conversation titles and content
    topics: set[str] = set()
    user_messages: list[str] = []

    for conv in conversations:
        if not isinstance(conv, dict):
            continue

        # Collect conversation titles as topics
        title = conv.get("title", "")
        if title and title != "New chat":
            topics.add(title)

        # Extract user messages from the mapping structure
        mapping = conv.get("mapping", {})
        if isinstance(mapping, dict):
            for _node_id, node in mapping.items():
                if not isinstance(node, dict):
                    continue
                message = node.get("message")
                if not message or not isinstance(message, dict):
                    continue
                author = message.get("author", {})
                if author.get("role") == "user":
                    content = message.get("content", {})
                    parts = content.get("parts", [])
                    for part in parts:
                        if isinstance(part, str) and len(part) > 10:
                            user_messages.append(part)

    # Build memory items from topics
    for topic in list(topics)[:50]:  # Cap at 50 topics
        result["memories"].append({
            "type": "topic",
            "content": f"User discussed: {topic}",
            "confidence": 0.5,
            "source": "imported_chatgpt",
        })

    # Extract preferences and patterns from user messages
    _extract_preferences(user_messages, result["memories"])

    return result


def _extract_preferences(
    messages: list[str],
    memories: list[dict[str, Any]],
) -> None:
    """Extract user preferences from message content."""
    import re

    preference_patterns = [
        (r"(?i)i (?:prefer|like|love|enjoy) (.+?)(?:\.|,|!|\?|$)", "preference"),
        (r"(?i)my (?:name|job|role) is (.+?)(?:\.|,|!|\?|$)", "identity"),
        (r"(?i)i work (?:at|as|for|in) (.+?)(?:\.|,|!|\?|$)", "identity"),
        (r"(?i)i live in (.+?)(?:\.|,|!|\?|$)", "identity"),
        (r"(?i)i(?:'m| am) (?:a |an )?(.+?)(?:\.|,|!|\?|$)", "identity"),
    ]

    seen: set[str] = set()

    for msg in messages[:200]:  # Process up to 200 messages
        for pattern, mem_type in preference_patterns:
            matches = re.findall(pattern, msg)
            for match in matches:
                value = match.strip()
                if 3 < len(value) < 100 and value.lower() not in seen:
                    seen.add(value.lower())
                    memories.append({
                        "type": mem_type,
                        "content": value,
                        "confidence": 0.5,
                        "source": "imported_chatgpt",
                    })

    # Cap total memories
    if len(memories) > 100:
        del memories[100:]
