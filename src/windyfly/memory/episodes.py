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

    # Compute the semantic embedding when sentence-transformers is
    # installed. Returns None if not available — schema column stays
    # NULL and search falls back to FTS5-only. Best-effort: never
    # blocks save on embed failure.
    embedding_blob = None
    embedding_model_name = None
    try:
        from windyfly.memory import embeddings as _emb
        if _emb.is_available():
            embedding_blob = _emb.embed(content)
            if embedding_blob is not None:
                embedding_model_name = _emb.model_name()
    except Exception:
        pass

    db.execute(
        """
        INSERT INTO episodes (id, role, content, session_id, summary,
                              token_count, cost_usd, emotional_context,
                              request_id, embedding, embedding_model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (episode_id, role, content, session_id, summary,
         token_count, cost_usd, emotional_context, request_id,
         embedding_blob, embedding_model_name),
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


def search_episodes_hybrid(
    db: Database,
    query: str,
    limit: int = 10,
    *,
    session_id: str | None = None,
    exclude_ids: set[str] | None = None,
    fts_weight: float = 1.0,
    semantic_weight: float = 1.0,
    semantic_pool: int = 50,
) -> list[dict]:
    """Combined keyword + semantic search via Reciprocal Rank Fusion.

    Runs FTS5 keyword search AND cosine-similarity over stored
    embeddings, then fuses the two ranked lists. RRF (Reciprocal
    Rank Fusion) is the standard technique: each doc scores
    1/(k + rank) in each list, and the combined score is the
    weighted sum. k=60 is the established constant from the
    Cormack/Clarke 2009 paper.

    When sentence-transformers isn't installed (or no episodes have
    embeddings yet), this gracefully falls back to FTS5-only —
    same behavior as ``search_episodes``.

    Args:
        db: Database instance.
        query: Search text.
        limit: Max results.
        session_id: Optional session filter.
        exclude_ids: Optional ID exclusion set.
        fts_weight: Weight for FTS5 ranks. Default 1.0.
        semantic_weight: Weight for semantic ranks. Default 1.0
            (equal blend). Set to 0 to disable semantic.
        semantic_pool: How many embedded episodes to score by cosine.
            Cosine over every episode is O(n × dim); we cap at the
            top-N most-recent embedded episodes so this stays fast on
            DBs with 50K+ episodes.

    Returns:
        List of episode dicts, RRF-ranked best first.
    """
    # FTS5 path always runs (it's the baseline).
    fts_hits = search_episodes(
        db, query, limit=max(limit * 3, 30),
        session_id=session_id, exclude_ids=exclude_ids,
    )

    # Semantic path runs only when embeddings module + episodes with
    # stored vectors both exist.
    semantic_hits: list[dict] = []
    if semantic_weight > 0 and query and query.strip():
        try:
            from windyfly.memory import embeddings as _emb
            if _emb.is_available():
                query_blob = _emb.embed(query)
                if query_blob is not None:
                    sql = (
                        "SELECT * FROM episodes "
                        "WHERE embedding IS NOT NULL"
                    )
                    params: list = []
                    if session_id is not None:
                        sql += " AND session_id = ?"
                        params.append(session_id)
                    if exclude_ids:
                        placeholders = ",".join("?" * len(exclude_ids))
                        sql += f" AND id NOT IN ({placeholders})"
                        params.extend(exclude_ids)
                    sql += " ORDER BY created_at DESC LIMIT ?"
                    params.append(semantic_pool)
                    candidates = db.fetchall(sql, tuple(params))

                    scored = []
                    for ep in candidates:
                        sim = _emb.cosine(query_blob, ep.get("embedding"))
                        scored.append((sim, ep))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    semantic_hits = [ep for _, ep in scored[:max(limit * 3, 30)]]
        except Exception:
            # Any failure → degrade silently to FTS5-only
            pass

    if not semantic_hits:
        return fts_hits[:limit]

    # ── Reciprocal Rank Fusion ──
    K = 60
    scores: dict[str, float] = {}
    by_id: dict[str, dict] = {}
    for rank, ep in enumerate(fts_hits):
        eid = ep["id"]
        scores[eid] = scores.get(eid, 0.0) + fts_weight / (K + rank + 1)
        by_id[eid] = ep
    for rank, ep in enumerate(semantic_hits):
        eid = ep["id"]
        scores[eid] = scores.get(eid, 0.0) + semantic_weight / (K + rank + 1)
        by_id.setdefault(eid, ep)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [by_id[eid] for eid, _ in ranked[:limit]]
