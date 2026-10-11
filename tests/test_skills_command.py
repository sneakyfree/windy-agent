"""/skills lists skills (it used to import functions that never existed and always said "Error")."""

from __future__ import annotations

import asyncio

from windyfly.memory.database import Database
from windyfly.memory.skills import save_skill


def _run(db: Database, text: str) -> str:
    from windyfly.commands import core
    from windyfly.commands.registry import parse_command, registry

    core.init_core(db=db, config={})
    return asyncio.run(registry.execute(parse_command(text), {"platform": "cli"}))


def _db() -> Database:
    db = Database(":memory:")
    promoted = save_skill(db, "greet", "Say hello warmly.", "playbook")
    save_skill(db, "draft", "Not ready yet.", "playbook")
    db.execute("UPDATE skills SET promoted = TRUE, description = NULL WHERE id = ?", (promoted,))
    db.commit()
    return db


def test_skills_lists_promoted_only():
    reply = _run(_db(), "/skills")
    assert not reply.startswith("Error")
    assert "Promoted skills" in reply and "greet" in reply and "draft" not in reply


def test_skills_all_lists_every_skill():
    reply = _run(_db(), "/skills all")
    assert not reply.startswith("Error")
    assert "All skills" in reply and "greet" in reply and "draft" in reply


def test_no_skills_says_so():
    assert _run(Database(":memory:"), "/skills") == "No skills found."
