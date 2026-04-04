"""To-do list — "Add 'buy groceries' to my list."

Simple persistent task list stored in SQLite. The LLM calls these
tools automatically when users mention tasks, lists, or to-dos.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from windyfly.memory.database import Database
    from windyfly.tools.registry import ToolRegistry

_TODOS_SQL = """
CREATE TABLE IF NOT EXISTS todos (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT DEFAULT 'medium',
    due_date DATETIME,
    completed BOOLEAN DEFAULT FALSE,
    completed_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def _ensure_table(db: Database) -> None:
    db.conn.executescript(_TODOS_SQL)


def add_todo(
    db: Database,
    title: str,
    priority: str = "medium",
    due_date: str | None = None,
    description: str | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    """Add a new to-do item."""
    _ensure_table(db)
    todo_id = str(uuid.uuid4())[:8]
    db.execute(
        "INSERT INTO todos (id, user_id, title, description, priority, due_date) VALUES (?, ?, ?, ?, ?, ?)",
        (todo_id, user_id, title, description, priority, due_date),
    )
    db.commit()
    return {
        "success": True,
        "id": todo_id,
        "message": f"Added to your list: {title}",
    }


def list_todos(
    db: Database,
    include_completed: bool = False,
    user_id: str = "default",
) -> dict[str, Any]:
    """List to-do items."""
    _ensure_table(db)
    if include_completed:
        rows = db.fetchall(
            "SELECT * FROM todos WHERE user_id = ? ORDER BY completed, priority DESC, created_at",
            (user_id,),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM todos WHERE user_id = ? AND completed = FALSE ORDER BY priority DESC, created_at",
            (user_id,),
        )

    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "title": r["title"],
            "priority": r["priority"],
            "due_date": r.get("due_date"),
            "completed": bool(r["completed"]),
        })

    if not items:
        return {"todos": [], "message": "Your to-do list is empty! 🎉"}

    lines = []
    for t in items:
        check = "✅" if t["completed"] else "○"
        pri = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(t["priority"], "")
        lines.append(f"{check} {pri} {t['title']}")

    return {"todos": items, "message": "\n".join(lines)}


def complete_todo(
    db: Database,
    todo_id_or_title: str,
    user_id: str = "default",
) -> dict[str, Any]:
    """Mark a to-do as completed by ID or title (fuzzy match)."""
    _ensure_table(db)

    # Try exact ID match
    row = db.fetchone(
        "SELECT * FROM todos WHERE id = ? AND user_id = ?",
        (todo_id_or_title, user_id),
    )

    # Fuzzy title match
    if not row:
        row = db.fetchone(
            "SELECT * FROM todos WHERE user_id = ? AND completed = FALSE AND title LIKE ? LIMIT 1",
            (user_id, f"%{todo_id_or_title}%"),
        )

    if not row:
        return {"success": False, "error": f"No matching to-do found: {todo_id_or_title}"}

    db.execute(
        "UPDATE todos SET completed = TRUE, completed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), row["id"]),
    )
    db.commit()
    return {"success": True, "message": f"Done! ✅ {row['title']}"}


def delete_todo(
    db: Database,
    todo_id_or_title: str,
    user_id: str = "default",
) -> dict[str, Any]:
    """Delete a to-do by ID or title."""
    _ensure_table(db)

    row = db.fetchone(
        "SELECT * FROM todos WHERE id = ? AND user_id = ?",
        (todo_id_or_title, user_id),
    )
    if not row:
        row = db.fetchone(
            "SELECT * FROM todos WHERE user_id = ? AND title LIKE ? LIMIT 1",
            (user_id, f"%{todo_id_or_title}%"),
        )

    if not row:
        return {"success": False, "error": f"No matching to-do found: {todo_id_or_title}"}

    db.execute("DELETE FROM todos WHERE id = ?", (row["id"],))
    db.commit()
    return {"success": True, "message": f"Removed: {row['title']}"}


def register_todo_tools(registry: ToolRegistry, db: Database) -> None:
    """Register to-do tools with the LLM tool registry."""
    registry.register(
        name="add_todo",
        description=(
            "Add an item to the user's to-do list. Use when they say "
            "'add X to my list', 'I need to...', 'don't forget to...'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The task to add"},
                "priority": {"type": "string", "description": "low, medium, or high"},
                "due_date": {"type": "string", "description": "Optional due date"},
            },
            "required": ["title"],
        },
        fn=lambda title, priority="medium", due_date=None: add_todo(db, title, priority, due_date),
    )

    registry.register(
        name="list_todos",
        description="Show the user's to-do list. Use when they ask 'what's on my list?' or 'what do I need to do?'.",
        parameters={
            "type": "object",
            "properties": {
                "include_completed": {"type": "boolean", "description": "Include completed items"},
            },
        },
        fn=lambda include_completed=False: list_todos(db, include_completed),
    )

    registry.register(
        name="complete_todo",
        description="Mark a to-do item as done. Match by title or ID.",
        parameters={
            "type": "object",
            "properties": {
                "todo_id_or_title": {"type": "string", "description": "The to-do ID or title text to match"},
            },
            "required": ["todo_id_or_title"],
        },
        fn=lambda todo_id_or_title: complete_todo(db, todo_id_or_title),
    )
