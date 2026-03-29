"""Intents CRUD and decay.

Manages user goals and intents with decay over time.
"""

from __future__ import annotations

import uuid
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.write_queue import Priority, WriteQueue


def create_intent(
    db: Database,
    description: str,
    *,
    user_id: str = "default",
    scope_id: str = "personal",
    origin: str = "user_said",
    priority: int = 5,
    autonomy_policy: str = "inform",
    linked_nodes: list[str] | None = None,
) -> str:
    """Create a new intent.

    Args:
        db: Database instance.
        description: Intent description.
        user_id: User ID.
        scope_id: Scope ('personal', 'work', 'project').
        origin: Origin ('user_said', 'inferred_from_chat').
        priority: 1-10 priority level.
        autonomy_policy: 'inform', 'ask', 'auto'.
        linked_nodes: Optional list of related node IDs.

    Returns:
        Generated intent ID.
    """
    import json
    intent_id = str(uuid.uuid4())
    nodes_json = json.dumps(linked_nodes) if linked_nodes else None

    db.execute(
        """
        INSERT INTO intents (id, user_id, scope_id, description, origin,
                             priority, autonomy_policy, linked_nodes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (intent_id, user_id, scope_id, description, origin,
         priority, autonomy_policy, nodes_json),
    )
    db.commit()
    return intent_id


def get_intent(db: Database, intent_id: str) -> dict[str, Any] | None:
    """Get an intent by ID."""
    return db.fetchone("SELECT * FROM intents WHERE id = ?", (intent_id,))


def surface_pending_intents(
    db: Database,
    user_id: str = "default",
) -> list[dict[str, Any]]:
    """Get inferred intents from the last 24 hours — the Intent Inbox.

    Returns:
        List of active inferred intents.
    """
    return db.fetchall(
        """
        SELECT * FROM intents
        WHERE status = 'active'
          AND origin = 'inferred_from_chat'
          AND user_id = ?
          AND created_at > datetime('now', '-24 hours')
        ORDER BY priority DESC, created_at DESC
        """,
        (user_id,),
    )


def complete_intent(db: Database, intent_id: str) -> None:
    """Mark an intent as completed."""
    db.execute(
        "UPDATE intents SET status = 'completed', last_touched = CURRENT_TIMESTAMP WHERE id = ?",
        (intent_id,),
    )
    db.commit()


def pause_intent(db: Database, intent_id: str) -> None:
    """Pause an intent."""
    db.execute(
        "UPDATE intents SET status = 'paused', last_touched = CURRENT_TIMESTAMP WHERE id = ?",
        (intent_id,),
    )
    db.commit()


def decay_intents(db: Database, write_queue: WriteQueue) -> None:
    """Apply decay to stale intents.

    Intents not touched in 7 days lose 5% of their decay score.
    Intents with decay_score < 0.3 are auto-paused.
    """
    def _decay():
        db.execute(
            """
            UPDATE intents SET decay_score = decay_score * 0.95
            WHERE status = 'active'
              AND last_touched < datetime('now', '-7 days')
            """,
        )
        db.execute(
            """
            UPDATE intents SET status = 'paused'
            WHERE status = 'active' AND decay_score < 0.3
            """,
        )
        db.commit()

    write_queue.enqueue(Priority.LOW, _decay)


def find_similar_intent(
    db: Database,
    description: str,
    *,
    user_id: str = "default",
    threshold: int = 3,
) -> dict[str, Any] | None:
    """Find an active intent with very similar description (prevent duplicates).

    Uses word-overlap heuristic — if 60%+ of words overlap with an existing
    active intent, it's considered a duplicate.

    Args:
        db: Database instance.
        description: New intent description to check.
        user_id: User ID.
        threshold: Minimum word overlap count.

    Returns:
        Existing similar intent dict, or None.
    """
    active = db.fetchall(
        "SELECT * FROM intents WHERE status = 'active' AND user_id = ? "
        "ORDER BY created_at DESC LIMIT 20",
        (user_id,),
    )
    desc_lower = description.lower().strip()
    desc_words = set(desc_lower.split())
    if not desc_words:
        return None

    for intent in active:
        existing = (intent.get("description") or "").lower().strip()
        existing_words = set(existing.split())
        if not existing_words:
            continue
        overlap = len(desc_words & existing_words)
        if overlap >= threshold and overlap >= len(desc_words) * 0.6:
            return intent

    return None
