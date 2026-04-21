"""Episode (conversation history) CRUD operations.

Handles saving, retrieving, and searching conversation episodes.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from windyfly.memory.database import Database


def save_episode(
    db: Database,
    role: str,
    content: str,
    *,
    session_id: str | None = None,
    summary: str | None = None,
    token_count: int | None = None,
    cost_usd: float | None = None,
    emotional_context: str | None = None,
    request_id: str | None = None,
) -> str:
    """Save a conversation episode to the database.

    Args:
        db: Database instance.
        role: Message role ('user', 'assistant', 'system').
        content: The message content.
        session_id: Optional session ID to group messages.
        summary: Optional summary of the message.
        token_count: Optional token count for this message.
        cost_usd: Optional cost in USD.
        emotional_context: Optional detected emotional state.
        request_id: Optional Wave 14 tracing correlation id. If None,
            falls back to the current contextvar so callers don't
            need to plumb it explicitly.

    Returns:
        The generated episode ID (UUID4).
    """
    if request_id is None:
        from windyfly.agent.tracing import get_request_id
        request_id = get_request_id()
    episode_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO episodes (id, role, content, session_id, summary,
                              token_count, cost_usd, emotional_context,
                              request_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (episode_id, role, content, session_id, summary,
         token_count, cost_usd, emotional_context, request_id),
    )
    db.commit()
    return episode_id


def get_recent_episodes(
    db: Database,
    limit: int = 20,
    session_id: str | None = None,
) -> list[dict]:
    """Get the most recent episodes, optionally filtered by session.

    Args:
        db: Database instance.
        limit: Max number of episodes to return.
        session_id: Optional session ID filter.

    Returns:
        List of episode dicts, most recent first.
    """
    if session_id:
        return db.fetchall(
            """
            SELECT * FROM episodes
            WHERE session_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
    return db.fetchall(
        """
        SELECT * FROM episodes
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
        """,
        (limit,),
    )


def search_episodes(
    db: Database,
    query: str,
    limit: int = 10,
) -> list[dict]:
    """Full-text search across episode content and summaries.

    Args:
        db: Database instance.
        query: Search query string.
        limit: Max results to return.

    Returns:
        List of matching episode dicts.
    """
    return db.fetchall(
        """
        SELECT * FROM episodes
        WHERE rowid IN (
            SELECT rowid FROM episodes_fts
            WHERE episodes_fts MATCH ?
        )
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (query, limit),
    )
