"""Knowledge node CRUD operations.

Nodes represent entities in the user's life-graph: people, preferences,
facts, beliefs, locations, etc.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from windyfly.memory.database import Database

logger = logging.getLogger(__name__)


def upsert_node(
    db: Database,
    type: str,
    name: str,
    *,
    metadata: dict[str, Any] | None = None,
    epistemic_status: str = "inferred",
    confidence: float = 1.0,
    source: str = "inferred",
    scope_id: str = "personal",
    valid_from: str | None = None,
    valid_until: str | None = None,
) -> str:
    """Insert or update a knowledge node.

    If a node with the same (type, name, scope_id) exists, update it.
    Otherwise, create a new node with a UUID4 id.

    Before updating an existing node, checks for conflicts and records
    them in the conflicts table via conflict_detector.

    Returns:
        The node ID.
    """
    metadata_json = json.dumps(metadata) if metadata else None

    # Check for existing node
    existing = db.fetchone(
        "SELECT id FROM nodes WHERE type = ? AND name = ? AND scope_id = ?",
        (type, name, scope_id),
    )

    if existing:
        node_id = existing["id"]

        # Check for conflict before overwriting
        from windyfly.memory.conflict_detector import check_for_conflict
        conflict = check_for_conflict(db, type, name, metadata_json or "")
        if conflict:
            logger.info(
                "Conflict detected on node %s/%s: old=%s new=%s (conflict_id=%s)",
                type, name,
                str(conflict["old_value"])[:50],
                str(conflict["new_value"])[:50],
                conflict["conflict_id"],
            )

        db.execute(
            """
            UPDATE nodes
            SET metadata = ?, epistemic_status = ?, confidence = ?,
                source = ?, valid_from = ?, valid_until = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (metadata_json, epistemic_status, confidence,
             source, valid_from, valid_until, node_id),
        )
    else:
        node_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO nodes (id, type, name, metadata, epistemic_status,
                               confidence, source, scope_id, valid_from, valid_until)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (node_id, type, name, metadata_json, epistemic_status,
             confidence, source, scope_id, valid_from, valid_until),
        )

    db.commit()
    return node_id


def get_all_nodes(
    db: Database,
    *,
    node_type: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """List nodes, optionally filtered by ``node_type``.

    Backs the ``/memory nodes [type]`` slash command — without this
    the command was silently returning ``"Error: cannot import name
    'get_all_nodes'"`` because the import was inside a swallow-all
    try/except. Recency-first; default 20 to keep slash-command
    replies bounded.
    """
    if node_type:
        return db.fetchall(
            "SELECT * FROM nodes WHERE type = ? ORDER BY updated_at DESC LIMIT ?",
            (node_type, limit),
        )
    return db.fetchall(
        "SELECT * FROM nodes ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    )


def count_nodes(db: Database) -> int:
    """Total node count — backs ``/memory stats``. Pure SQL."""
    row = db.fetchone("SELECT COUNT(*) AS n FROM nodes")
    return int((row or {}).get("n", 0))


def get_nodes_by_type(
    db: Database,
    type: str,
    limit: int = 10,
) -> list[dict]:
    """Get nodes filtered by type.

    Args:
        db: Database instance.
        type: Node type to filter by (e.g., 'person', 'preference', 'fact').
        limit: Max results.

    Returns:
        List of node dicts.
    """
    return db.fetchall(
        "SELECT * FROM nodes WHERE type = ? ORDER BY updated_at DESC LIMIT ?",
        (type, limit),
    )


def search_nodes(
    db: Database,
    query: str,
    limit: int = 10,
) -> list[dict]:
    """Search nodes by name and metadata content.

    Splits ``query`` into individual terms and ORs the LIKE clauses
    across all terms. The previous behavior treated the whole query as
    a single contiguous substring, which silently returned zero hits
    for the common "what do you know about X" pattern (e.g.,
    `_extract_keywords` produces `"know polly"`, no node name contains
    that exact substring, ranker returns nothing, agent loses all
    seeded context). Surfaced by Grant's first /seed dogfood test.

    A node matches if ANY term hits its name OR metadata. Ranking by
    updated_at preserves recency. Empty / whitespace-only query
    returns nothing rather than the whole table.
    """
    terms = [t for t in (query or "").split() if t]
    if not terms:
        return []
    likes = [f"%{t}%" for t in terms]

    # Tier 1: name match — high precision. Each term ORed.
    name_clauses = " OR ".join(["name LIKE ?"] * len(terms))
    name_hits = db.fetchall(
        f"SELECT * FROM nodes WHERE {name_clauses} "
        f"ORDER BY updated_at DESC LIMIT ?",
        (*likes, limit),
    )
    if name_hits:
        return name_hits

    # Tier 2 fallback: metadata match — broader, but only when nothing
    # in the name matches. Prevents the prompt-bloat case where a
    # random unrelated message ("something completely unrelated") pulls
    # nodes whose metadata happens to contain a common word.
    meta_clauses = " OR ".join(["JSON_EXTRACT(metadata, '$') LIKE ?"] * len(terms))
    return db.fetchall(
        f"SELECT * FROM nodes WHERE {meta_clauses} "
        f"ORDER BY updated_at DESC LIMIT ?",
        (*likes, limit),
    )


def get_node(db: Database, node_id: str) -> dict | None:
    """Get a single node by ID.

    Args:
        db: Database instance.
        node_id: The node's UUID.

    Returns:
        Node dict or None if not found.
    """
    return db.fetchone("SELECT * FROM nodes WHERE id = ?", (node_id,))
