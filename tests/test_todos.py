"""Tests for the to-do list tool."""

from windyfly.memory.database import Database
from windyfly.tools.todos import add_todo, complete_todo, delete_todo, list_todos


def _db():
    return Database(":memory:")


def test_add_todo():
    db = _db()
    result = add_todo(db, "Buy groceries")
    assert result["success"] is True
    assert "Buy groceries" in result["message"]
    db.close()


def test_list_empty():
    db = _db()
    result = list_todos(db)
    assert result["todos"] == []
    assert "empty" in result["message"].lower()
    db.close()


def test_list_with_items():
    db = _db()
    add_todo(db, "Item 1")
    add_todo(db, "Item 2", priority="high")
    result = list_todos(db)
    assert len(result["todos"]) == 2
    priorities = {t["priority"] for t in result["todos"]}
    assert "high" in priorities
    assert "medium" in priorities
    db.close()


def test_complete_by_title():
    db = _db()
    add_todo(db, "Buy groceries")
    result = complete_todo(db, "groceries")
    assert result["success"] is True
    assert "Buy groceries" in result["message"]
    # Should not appear in active list
    active = list_todos(db)
    assert len(active["todos"]) == 0
    db.close()


def test_complete_by_id():
    db = _db()
    r = add_todo(db, "Test item")
    result = complete_todo(db, r["id"])
    assert result["success"] is True
    db.close()


def test_complete_nonexistent():
    db = _db()
    result = complete_todo(db, "does not exist")
    assert result["success"] is False
    db.close()


def test_delete_todo():
    db = _db()
    add_todo(db, "Delete me")
    result = delete_todo(db, "Delete me")
    assert result["success"] is True
    assert list_todos(db)["todos"] == []
    db.close()


def test_delete_nonexistent():
    db = _db()
    result = delete_todo(db, "nope")
    assert result["success"] is False
    db.close()


def test_include_completed():
    db = _db()
    add_todo(db, "Task 1")
    add_todo(db, "Task 2")
    complete_todo(db, "Task 1")
    active = list_todos(db, include_completed=False)
    assert len(active["todos"]) == 1
    all_items = list_todos(db, include_completed=True)
    assert len(all_items["todos"]) == 2
    db.close()


def test_priority_defaults_to_medium():
    db = _db()
    r = add_todo(db, "Default priority")
    items = list_todos(db)
    assert items["todos"][0]["priority"] == "medium"
    db.close()
