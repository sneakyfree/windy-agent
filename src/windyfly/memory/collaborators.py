"""CRUD operations for the collaborators table (Wave 6 #1).

Long-running named sub-agents that persist across sessions, optionally
sharing filtered slices of the parent's memory. The Hermes-killer
feature: their delegate_task is depth-2 max with no memory inheritance;
ours has a "research" collaborator that's been around for 3 weeks and
knows your research preferences (depth, formatting, source trust).

Memory share policy is a JSON column with this shape:
  {
    "include_personality": bool,        # see parent's persona
    "node_types": ["research_topic", ...],  # which knowledge-graph types
    "topic_keywords": ["mortgage", ...],     # filter by keyword in node name
    "include_intents": bool                  # see parent's active intents
  }
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.write_queue import Priority, WriteQueue

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_POLICY: dict[str, Any] = {
    "include_personality": True,
    "node_types": [],
    "topic_keywords": [],
    "include_intents": False,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_collaborator(
    db: Database,
    write_queue: WriteQueue,
    *,
    name: str,
    persona_prompt: str,
    parent_user_id: str = "default",
    memory_share_policy: dict[str, Any] | None = None,
    band: str = "USER",
    model: str | None = None,
    daily_budget_usd: float = 1.0,
    max_context_tokens: int = 8000,
) -> str:
    """Create a new collaborator. Returns the collaborator id.

    Raises ValueError if a collaborator with the same name already
    exists for this user (unique constraint on (name, parent_user_id)
    where archived_at IS NULL).
    """
    if not name or not name.strip():
        raise ValueError("collaborator name cannot be empty")
    if not persona_prompt or not persona_prompt.strip():
        raise ValueError("collaborator persona_prompt cannot be empty")

    existing = get_collaborator_by_name(db, name, parent_user_id)
    if existing is not None:
        raise ValueError(
            f"collaborator {name!r} already exists for user "
            f"{parent_user_id!r} (id {existing['id']}). Archive it "
            "first to recreate, or use a different name."
        )

    collab_id = uuid.uuid4().hex
    policy = json.dumps(memory_share_policy or DEFAULT_MEMORY_POLICY)

    write_queue.enqueue(
        Priority.HIGH,
        _do_insert,
        db, collab_id, name, parent_user_id, persona_prompt,
        band, policy, model, daily_budget_usd, max_context_tokens,
    )
    return collab_id


def _do_insert(
    db: Database,
    collab_id: str, name: str, parent_user_id: str, persona_prompt: str,
    band: str, policy: str, model: str | None,
    daily_budget_usd: float, max_context_tokens: int,
) -> None:
    db.execute(
        """
        INSERT INTO collaborators (
            id, name, parent_user_id, persona_prompt, band,
            memory_share_policy, model, daily_budget_usd, max_context_tokens
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (collab_id, name, parent_user_id, persona_prompt, band, policy,
         model, daily_budget_usd, max_context_tokens),
    )


def get_collaborator_by_name(
    db: Database, name: str, parent_user_id: str = "default",
) -> dict[str, Any] | None:
    return db.fetchone(
        """
        SELECT * FROM collaborators
        WHERE name = ? AND parent_user_id = ? AND archived_at IS NULL
        """,
        (name, parent_user_id),
    )


def list_collaborators(
    db: Database, parent_user_id: str = "default",
    *, include_archived: bool = False,
) -> list[dict[str, Any]]:
    if include_archived:
        return db.fetchall(
            "SELECT * FROM collaborators WHERE parent_user_id = ? "
            "ORDER BY last_used_at DESC NULLS LAST, created_at DESC",
            (parent_user_id,),
        )
    return db.fetchall(
        "SELECT * FROM collaborators WHERE parent_user_id = ? "
        "AND archived_at IS NULL "
        "ORDER BY last_used_at DESC NULLS LAST, created_at DESC",
        (parent_user_id,),
    )


def archive_collaborator(
    db: Database, write_queue: WriteQueue,
    *, name: str, parent_user_id: str = "default",
) -> bool:
    """Soft-delete by setting archived_at. Returns True if anything changed."""
    existing = get_collaborator_by_name(db, name, parent_user_id)
    if existing is None:
        return False

    write_queue.enqueue(
        Priority.HIGH,
        _do_archive,
        db, existing["id"], _now_iso(),
    )
    return True


def _do_archive(db: Database, collab_id: str, archived_at: str) -> None:
    db.execute(
        "UPDATE collaborators SET archived_at = ? WHERE id = ?",
        (archived_at, collab_id),
    )


def record_use(
    db: Database, write_queue: WriteQueue, *, collaborator_id: str,
) -> None:
    """Bump use_count and last_used_at after a successful delegation.

    HIGH priority because /pulse, /caps, and the future Wave 7
    optimizer all read these stats — stale reads would mislead.
    """
    write_queue.enqueue(
        Priority.HIGH,
        _do_record_use,
        db, collaborator_id, _now_iso(),
    )


def _do_record_use(db: Database, collab_id: str, ts: str) -> None:
    db.execute(
        "UPDATE collaborators SET use_count = use_count + 1, last_used_at = ? "
        "WHERE id = ?",
        (ts, collab_id),
    )


def parse_memory_policy(raw: str) -> dict[str, Any]:
    """Decode the JSON policy column with safe fallback."""
    try:
        loaded = json.loads(raw or "{}")
        return {**DEFAULT_MEMORY_POLICY, **loaded}
    except json.JSONDecodeError:
        logger.warning("Malformed memory_share_policy JSON: %r", raw)
        return DEFAULT_MEMORY_POLICY.copy()
