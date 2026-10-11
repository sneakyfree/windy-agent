"""Conflict detector — detect contradictions in knowledge and HOLD them for the owner.

When an update contradicts an existing node, the node keeps its current value and a conflicts row
holds the proposed one (status 'pending') until the owner chooses (strand C4.6/C4.7):

- ``resolve_conflict(..., keep_new=True)`` applies the held value to the node;
- ``resolve_conflict(..., keep_new=False)`` leaves the node as it is.

The owner is told with facts, not advice: an owner turn's prompt lists pending conflicts
(agent/prompt.py), and the owner answers via the ``memory.resolve_conflict`` tool or
``/conflicts keep <id> new|old``.

Rows written before migration 15 have status 'unresolved' and no ``proposed``: the old code had
already overwritten the node with their new value, so "keep old" puts the old value back.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from windyfly.memory.database import Database

# The agent's own records: rewritten on purpose (newest wins), never facts to ask the owner about.
# A turnover letter or a re-composed journal day is not a contradiction.
AGENT_RECORD_TYPES = frozenset({"turnover_letter", "chronicle_journal", "self_assessment"})

# The owner's own direct words: the owner IS the chooser, so these apply at once and are never
# held. user_explicit = /remember (commands are owner-only); owner_stated = facts read from an
# OWNER-band turn. Everything else (other senders' messages, mail, SMS, imports, the agent's
# inferences) is held when it contradicts. Sources are code constants: no tool lets the model
# set one.
OWNER_DIRECT_SOURCES = frozenset({"user_explicit", "owner_stated"})

PENDING = "pending"          # held: the node still has the old value
LEGACY_UNRESOLVED = "unresolved"  # pre-migration-15 row: the new value was already applied
_OPEN_STATUSES = (PENDING, LEGACY_UNRESOLVED)

_MIN_PREFIX = 4


def _normalize(value: Any) -> str:
    """Same value, any key order or spacing, compares equal."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    text = str(value)
    try:
        return json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return text.strip()


def check_for_conflict(
    db: Database,
    node_type: str,
    node_name: str,
    new_value: str,
    *,
    scope_id: str | None = None,
    proposed: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Check if a new value contradicts an existing node; record a PENDING conflict if so.

    Args:
        db: Database instance.
        node_type: Node type.
        node_name: Node name.
        new_value: The new proposed value/metadata (JSON string).
        scope_id: When given, compare against the node in this scope only.
        proposed: The full update being held (metadata, epistemic_status, confidence, source,
            valid_from, valid_until). Defaults to just the metadata.

    Returns:
        Conflict dict if found (or the already-pending one for the same proposal), None otherwise.
    """
    if scope_id is None:
        existing = db.fetchone(
            "SELECT * FROM nodes WHERE type = ? AND name = ?",
            (node_type, node_name),
        )
    else:
        existing = db.fetchone(
            "SELECT * FROM nodes WHERE type = ? AND name = ? AND scope_id = ?",
            (node_type, node_name, scope_id),
        )

    if not existing:
        return None

    old_value = existing.get("metadata") or ""
    if isinstance(old_value, dict):
        old_value = json.dumps(old_value)

    # The same value repeated (any key order / spacing) is not a conflict.
    if _normalize(old_value) == _normalize(new_value):
        return None

    # Simple conflict: values differ
    if old_value and new_value and old_value != new_value:
        # Semantic check: if word overlap > 70%, likely same fact expressed differently
        old_words = set(old_value.lower().split())
        new_words = set(new_value.lower().split())
        if old_words and new_words:
            overlap = len(old_words & new_words) / max(len(old_words), len(new_words))
            if overlap > 0.7:
                # Similar enough — update silently, no conflict
                return None

        # The same proposal repeated while it is already held: no second row.
        for row in db.fetchall(
            "SELECT id, new_value FROM conflicts WHERE node_id = ? AND resolution_status = ?",
            (existing["id"], PENDING),
        ):
            if _normalize(row.get("new_value")) == _normalize(new_value):
                return {
                    "conflict_id": row["id"],
                    "node_id": existing["id"],
                    "old_value": old_value,
                    "new_value": new_value,
                    "repeated": True,
                }

        held = dict(proposed) if proposed else {"metadata": new_value}
        conflict_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO conflicts (id, node_id, old_value, new_value, resolution_status, proposed)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conflict_id, existing["id"], str(old_value), str(new_value), PENDING,
             json.dumps(held)),
        )
        db.commit()

        return {
            "conflict_id": conflict_id,
            "node_id": existing["id"],
            "old_value": old_value,
            "new_value": new_value,
        }

    return None


def find_conflict(db: Database, conflict_id: str) -> dict[str, Any] | None:
    """A conflict by full id or by a unique prefix (at least 4 characters, as the prompt shows 8)."""
    cid = (conflict_id or "").strip().lstrip("#")
    if not cid:
        return None
    row = db.fetchone("SELECT * FROM conflicts WHERE id = ?", (cid,))
    if row or len(cid) < _MIN_PREFIX:
        return row
    escaped = cid.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = db.fetchall(
        "SELECT * FROM conflicts WHERE id LIKE ? ESCAPE '\\' LIMIT 2", (escaped + "%",)
    )
    return rows[0] if len(rows) == 1 else None


def resolve_conflict(
    db: Database,
    conflict_id: str,
    resolution: str = "",
    keep_new: bool = False,
    *,
    resolved_by: str = "owner",
) -> dict[str, Any]:
    """Resolve a conflict with the owner's choice.

    Args:
        db: Database instance.
        conflict_id: Conflict to resolve (full id or unique prefix).
        resolution: Optional free-text note.
        keep_new: True applies the held value to the node; False leaves the node unchanged
            (for a pre-migration row whose new value was already applied, the old value is put back).
        resolved_by: Who chose, recorded on the row.

    Returns:
        ``{"ok": True, "conflict_id", "node", "kept", "value"}`` or ``{"ok": False, "error"}``.
    """
    conflict = find_conflict(db, conflict_id)
    if not conflict:
        return {"ok": False, "error": f"no conflict with id {conflict_id!r}"}
    if conflict.get("resolution_status") not in _OPEN_STATUSES:
        return {"ok": False, "error": f"conflict {conflict['id'][:8]} was already resolved",
                "kept": conflict.get("kept")}

    node_id = conflict.get("node_id")
    node = db.fetchone("SELECT * FROM nodes WHERE id = ?", (node_id,)) if node_id else None
    legacy = conflict.get("resolution_status") == LEGACY_UNRESOLVED or not conflict.get("proposed")

    if node is not None:
        if keep_new and not legacy:
            try:
                held = json.loads(conflict["proposed"])
            except (json.JSONDecodeError, TypeError):
                held = {}
            if not isinstance(held, dict):
                held = {}
            # Fields the held update did not carry keep the node's current value.
            fields = ("epistemic_status", "confidence", "source", "valid_from", "valid_until")
            values = [held[f] if f in held else node.get(f) for f in fields]
            db.execute(
                """
                UPDATE nodes
                SET metadata = ?, epistemic_status = ?, confidence = ?,
                    source = ?, valid_from = ?, valid_until = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (held.get("metadata", conflict.get("new_value")), *values, node_id),
            )
        elif keep_new and legacy:
            # Pre-migration row: the new value is (normally) already on the node.
            if _normalize(node.get("metadata")) != _normalize(conflict.get("new_value")):
                db.execute(
                    "UPDATE nodes SET metadata = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (conflict.get("new_value"), node_id),
                )
        elif legacy and _normalize(node.get("metadata")) == _normalize(conflict.get("new_value")):
            # Pre-migration row whose new value overwrote the old one: put the old value back.
            db.execute(
                "UPDATE nodes SET metadata = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (conflict.get("old_value"), node_id),
            )
        # keep old on a held row: the node already has the old value; nothing to write.

    kept = "new" if keep_new else "old"
    db.execute(
        """
        UPDATE conflicts SET
            resolution_status = 'user_resolved',
            user_resolved = TRUE,
            resolved_at = CURRENT_TIMESTAMP,
            kept = ?,
            resolution = ?,
            resolved_by = ?
        WHERE id = ?
        """,
        (kept, resolution or None, resolved_by, conflict["id"]),
    )
    db.commit()

    after = db.fetchone("SELECT metadata FROM nodes WHERE id = ?", (node_id,)) if node_id else None
    return {
        "ok": True,
        "conflict_id": conflict["id"],
        "node": (node or {}).get("name"),
        "kept": kept,
        "value": (after or {}).get("metadata"),
    }


def get_unresolved_conflicts(db: Database) -> list[dict[str, Any]]:
    """Every conflict the owner has not chosen on yet (held + pre-migration rows), newest first."""
    return db.fetchall(
        """
        SELECT c.*, n.type AS node_type, n.name AS node_name
        FROM conflicts c LEFT JOIN nodes n ON n.id = c.node_id
        WHERE c.resolution_status IN (?, ?)
        ORDER BY c.created_at DESC, c.rowid DESC
        """,
        _OPEN_STATUSES,
    )


def get_pending_conflicts(db: Database, limit: int | None = None) -> list[dict[str, Any]]:
    """Held conflicts (the node still has the old value), newest first."""
    sql = """
        SELECT c.*, n.type AS node_type, n.name AS node_name
        FROM conflicts c LEFT JOIN nodes n ON n.id = c.node_id
        WHERE c.resolution_status = ?
        ORDER BY c.created_at DESC, c.rowid DESC
    """
    if limit is not None:
        return db.fetchall(sql + " LIMIT ?", (PENDING, int(limit)))
    return db.fetchall(sql, (PENDING,))
