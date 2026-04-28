"""Prompt assembly for the Windy Fly agent.

Assembles the full message list for an LLM call:
system prompt (personality + mode), memory context (recent episodes),
relevant knowledge nodes, and the user's current message.
"""

from __future__ import annotations

import json
from typing import Any

from windyfly.control_panel import get_sliders
from windyfly.memory.database import Database
from windyfly.memory.episodes import get_recent_episodes
from windyfly.memory.nodes import get_nodes_by_type, search_nodes
from windyfly.personality.engine import build_personality_block, get_mode_override, load_soul


def _is_first_contact(db: Database) -> bool:
    """True if the bot has zero prior memory of any kind.

    Found via stress harness v6 Notebook test 2026-04-27: with a
    truly virgin DB (episodes=0, nodes=0), the bot's first reply
    opened with "Welcome back! Great to have you here" — the LLM
    defaults to familiarity language even when there's no memory
    to back it up. For a brand-new user's first ever message, this
    feels like the bot is play-acting, not actually remembering.

    Detection: episodes table empty AND nodes table empty. If
    either has rows, the bot has SOMETHING to anchor familiarity
    on (could be other-session history, extracted facts, etc.) and
    we let the personality block drive tone normally.
    """
    try:
        ep_row = db.fetchone("SELECT COUNT(*) AS c FROM episodes")
        nd_row = db.fetchone("SELECT COUNT(*) AS c FROM nodes")
    except Exception:
        # If the schema isn't ready yet, default to non-first-contact
        # — the personality block will drive tone, and the next turn
        # will have the row.
        return False
    n_eps = (ep_row or {}).get("c", 0)
    n_nodes = (nd_row or {}).get("c", 0)
    return n_eps == 0 and n_nodes == 0


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

    # First-contact guard: when the bot has no prior memory at all,
    # the LLM's default warmth kicks in and produces "welcome back" /
    # "good to see you again" even though it has nothing to remember.
    # This is a real product issue for grandma's first interaction —
    # the bot needs to know it's meeting her for the first time.
    if _is_first_contact(db):
        system_parts.append(
            "FIRST CONTACT: You have no prior memory of this user — "
            "no episodes, no extracted facts, no turnover letter. "
            "They have never spoken with you before. Greet them as a "
            "brand-new acquaintance. DO NOT use 'welcome back', 'good "
            "to see you again', 'as we discussed', 'picking up where "
            "we left off', or ANY phrase implying prior interaction. "
            "Introduce yourself naturally if appropriate."
        )

    messages.append({
        "role": "system",
        "content": "\n\n".join(system_parts),
    })

    # 1.5. Turnover letter — load the most recent one on session start
    turnover_letters = get_nodes_by_type(db, "turnover_letter", limit=1)
    if turnover_letters:
        letter = turnover_letters[0]
        meta = letter.get("metadata", "{}")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        summary = meta.get("summary", letter.get("name", "")) if isinstance(meta, dict) else str(meta)
        if summary:
            messages.append({
                "role": "system",
                "content": f"## Last Session Handoff\n{summary}",
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

    # 2.5. Relationship moments — shared emotional experiences
    moments = get_nodes_by_type(db, "relationship_moment", limit=10)
    if moments:
        moment_lines = ["## Shared Experiences:"]
        for m in moments:
            meta = m.get("metadata", "{}")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            summary = meta.get("summary", m.get("name", "")) if isinstance(meta, dict) else str(meta)
            if summary:
                moment_lines.append(f"- {summary}")
        if len(moment_lines) > 1:
            messages.append({
                "role": "system",
                "content": "\n".join(moment_lines),
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
