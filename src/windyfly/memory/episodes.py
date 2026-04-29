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
    *,
    session_id: str | None = None,
    exclude_ids: set[str] | None = None,
) -> list[dict]:
    """Full-text search across episode content and summaries.

    Args:
        db: Database instance.
        query: Search query string. Tokens are OR'd so any one match
            surfaces the episode (FTS5 default is AND, which is too
            restrictive for recall use).
        limit: Max results to return.
        session_id: Optional filter to a single session. Used by the
            prompt assembly to find earlier-in-this-conversation
            episodes that fell out of the recent-N window.
        exclude_ids: Optional set of episode IDs to exclude. Used to
            avoid duplicating episodes already injected via
            ``get_recent_episodes``.

    Returns:
        List of matching episode dicts, most-recent first.
    """
    import re
    # Sanitize tokens to FTS5-safe phrase queries. Strip non-word
    # characters so user input can't smuggle FTS5 syntax (operators
    # like AND/OR/NOT, quotes, asterisks). Then quote each token as
    # a phrase so "win" is literal, not a substring or prefix match.
    tokens = [re.sub(r"\W+", "", t) for t in query.split()]
    tokens = [t for t in tokens if len(t) >= 2][:6]  # keep small, focused
    if not tokens:
        return []
    fts_query = " OR ".join(f'"{t}"' for t in tokens)

    # JOIN to FTS table so we can ORDER BY rank (bm25). Pre-fix the
    # query ordered by created_at DESC, which meant older but
    # highly-relevant episodes got pushed past LIMIT by newer but
    # noisy matches. v9 hit 78% recall pre-rank-fix; surfacing
    # bm25 brings the older establishing facts to the top.
    sql = (
        "SELECT episodes.* FROM episodes"
        " JOIN episodes_fts ON episodes.rowid = episodes_fts.rowid"
        " WHERE episodes_fts MATCH ?"
    )
    params: list = [fts_query]

    if session_id is not None:
        sql += " AND episodes.session_id = ?"
        params.append(session_id)

    if exclude_ids:
        placeholders = ",".join("?" * len(exclude_ids))
        sql += f" AND episodes.id NOT IN ({placeholders})"
        params.extend(exclude_ids)

    # ORDER BY rank uses FTS5's built-in bm25 scoring (lower = better
    # match). Tiebreak on created_at DESC so among equally-relevant
    # episodes we prefer the more recent one.
    sql += " ORDER BY rank, episodes.created_at DESC LIMIT ?"
    params.append(limit)

    try:
        return db.fetchall(sql, tuple(params))
    except Exception:
        # FTS5 syntax errors (rare after sanitization) or a missing
        # FTS table on a corrupt DB shouldn't break the prompt
        # assembly path. Caller falls back to recent-only.
        return []
