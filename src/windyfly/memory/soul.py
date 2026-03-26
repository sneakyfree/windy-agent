"""Soul/personality CRUD operations.

Stores personality traits, slider values, and identity settings
in the soul table.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from windyfly.memory.database import Database


def upsert_soul(
    db: Database,
    key: str,
    value: str,
    *,
    source: str = "default",
    user_id: str = "default",
) -> str:
    """Insert or update a soul key-value pair.

    If a soul row with the same (user_id, key) exists, update it
    and increment the version. Otherwise, create a new row.

    Returns:
        The soul row ID.
    """
    existing = db.fetchone(
        "SELECT id, version FROM soul WHERE user_id = ? AND key = ?",
        (user_id, key),
    )

    if existing:
        soul_id = existing["id"]
        new_version = existing["version"] + 1
        db.execute(
            """
            UPDATE soul
            SET value = ?, version = ?, source = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (value, new_version, source, soul_id),
        )
    else:
        soul_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO soul (id, user_id, key, value, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (soul_id, user_id, key, value, source),
        )

    db.commit()
    return soul_id


def get_soul(
    db: Database,
    key: str,
    user_id: str = "default",
) -> dict | None:
    """Get a single soul value by key.

    Args:
        db: Database instance.
        key: The soul key to look up.
        user_id: User ID (default: 'default').

    Returns:
        Soul row dict or None.
    """
    return db.fetchone(
        "SELECT * FROM soul WHERE user_id = ? AND key = ?",
        (user_id, key),
    )


def get_all_soul(
    db: Database,
    user_id: str = "default",
) -> list[dict]:
    """Get all soul key-value pairs for a user.

    Args:
        db: Database instance.
        user_id: User ID (default: 'default').

    Returns:
        List of soul row dicts.
    """
    return db.fetchall(
        "SELECT * FROM soul WHERE user_id = ? ORDER BY key",
        (user_id,),
    )
