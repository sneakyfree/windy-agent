"""Knowledge node CRUD operations.

Nodes represent entities in the user's life-graph: people, preferences,
facts, beliefs, locations, etc.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from windyfly.memory.database import Database

import json


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

    Uses LIKE search on name and JSON_EXTRACT on metadata.

    Args:
        db: Database instance.
        query: Search string.
        limit: Max results.

    Returns:
        List of matching node dicts.
    """
    like_query = f"%{query}%"
    return db.fetchall(
        """
        SELECT * FROM nodes
        WHERE name LIKE ?
           OR JSON_EXTRACT(metadata, '$') LIKE ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (like_query, like_query, limit),
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
