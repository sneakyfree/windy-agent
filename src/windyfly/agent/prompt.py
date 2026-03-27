"""Prompt assembly for the Windy Fly agent.

Assembles the full message list for an LLM call:
system prompt (personality + mode), memory context (recent episodes),
relevant knowledge nodes, and the user's current message.
"""

from __future__ import annotations

from typing import Any

from windyfly.control_panel import get_sliders
from windyfly.memory.database import Database
from windyfly.memory.episodes import get_recent_episodes
from windyfly.memory.nodes import search_nodes
from windyfly.personality.engine import build_personality_block, get_mode_override, load_soul


def assemble_prompt(
    config: dict[str, Any],
    db: Database,
    user_message: str,
    session_id: str,
    *,
    mode: str = "companion",
) -> list[dict[str, str]]:
    """Assemble the full prompt for an LLM call.

    Args:
        config: Loaded config dict.
        db: Database instance.
        user_message: The user's current message.
        session_id: Current session ID.
        mode: Agent mode (companion/focused/neutral).

    Returns:
        List of message dicts ready for LLM API.
    """
    messages: list[dict[str, str]] = []

    # 1. System message: personality + mode override
    personality_config = config.get("personality", {})
    soul_path = personality_config.get("soul_path", "SOUL.md")
    soul_text = load_soul(soul_path)

    personality_block = build_personality_block(soul_text, personality_config)

    system_parts = [personality_block]

    mode_override = get_mode_override(mode)
    if mode_override:
        system_parts.append(mode_override)

    # Add epistemic instruction
    system_parts.append(
        "When you state a fact from memory, indicate your confidence level. "
        "If a fact is marked INFERRED, say so."
    )

    messages.append({
        "role": "system",
        "content": "\n\n".join(system_parts),
    })

    # 2. Memory context: relevant knowledge nodes
    max_nodes = config.get("memory", {}).get("max_nodes_per_context", 10)

    # Read epistemic strictness slider for node filtering
    sliders = get_sliders(db, config_defaults=personality_config)
    strictness = sliders.get("epistemic_strictness", 5)

    # Extract keywords from user message for node search
    keywords = _extract_keywords(user_message)
    if keywords:
        relevant_nodes = search_nodes(db, keywords, limit=max_nodes)

        # Filter nodes by epistemic strictness
        if relevant_nodes and strictness > 9:
            # Only verified and user_stated
            relevant_nodes = [
                n for n in relevant_nodes
                if n.get("epistemic_status") in ("verified", "user_stated")
            ]
        elif relevant_nodes and strictness > 7:
            # Exclude speculative and inferred
            relevant_nodes = [
                n for n in relevant_nodes
                if n.get("epistemic_status") not in ("speculative", "inferred")
            ]

        if relevant_nodes:
            node_lines = ["## Relevant Knowledge:"]
            for node in relevant_nodes:
                status_label = f"[{node.get('epistemic_status', 'unknown').upper()}]"
                node_lines.append(
                    f"- {status_label} {node['type']}: {node['name']}"
                    + (f" — {node.get('metadata', '')}" if node.get("metadata") else "")
                )
            messages.append({
                "role": "system",
                "content": "\n".join(node_lines),
            })

    # 3. Conversation history: recent episodes from this session
    # context_window slider: 0 → 5 episodes, 10 → 55 episodes
    context_window = sliders.get("context_window", 5)
    max_episodes = 5 + (context_window * 5)
    recent = get_recent_episodes(db, limit=max_episodes, session_id=session_id)

    # Episodes come back most-recent-first; reverse for chronological order
    for episode in reversed(recent):
        messages.append({
            "role": episode["role"],
            "content": episode["content"],
        })

    # 4. Current user message
    messages.append({
        "role": "user",
        "content": user_message,
    })

    return messages


def _extract_keywords(message: str, min_length: int = 3) -> str:
    """Extract meaningful keywords from a user message for node search.

    Simple approach: filter out very short words and common stopwords.

    Args:
        message: The user's message.
        min_length: Minimum word length to include.

    Returns:
        Space-joined keyword string for LIKE search.
    """
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "this", "that", "these", "those", "and", "but", "or", "nor",
        "not", "for", "with", "about", "what", "how", "why", "when",
        "where", "who", "which", "your", "you", "my", "me", "i",
    }
    words = message.lower().split()
    keywords = [w.strip(".,!?;:'\"") for w in words if len(w) >= min_length and w.lower() not in stopwords]
    return " ".join(keywords[:5])  # Limit to 5 keywords
