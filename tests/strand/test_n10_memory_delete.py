"""Strand gene N10: the owner sees and deletes the agent's memory."""

from __future__ import annotations

import asyncio
from pathlib import Path

from windyfly.channels.base import handle_incoming
from windyfly.commands.setup import init_all_commands
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.memory.failures import log_failure
from windyfly.memory.nodes import upsert_node

CANARY = "zqxcanary7731"
CLI = {"platform": "cli", "channel_id": "n10"}


def _say(text: str) -> str:
    ok, out = asyncio.run(handle_incoming(text, dict(CLI)))
    assert ok
    return out


def _raw_bytes(path: Path) -> bytes:
    data = b""
    for p in (path, Path(f"{path}-wal")):
        if p.exists():
            data += p.read_bytes()
    return data


def test_n10_forget_finds_and_deletes_after_a_yes_leaving_nothing_readable(tmp_path):
    from windyfly.commands.core import wire_runtime

    path = tmp_path / "memory.db"
    db = Database(str(path))
    try:
        init_all_commands(db=db, config={})
        wire_runtime(db=db)
        save_episode(db, "user", f"my secret plan is {CANARY} for the bakery", session_id="s1")
        save_episode(db, "assistant", "noted, unrelated reply", session_id="s1")
        upsert_node(db, "fact", f"secret plan {CANARY}")
        log_failure(db, "factual_error", f"No, {CANARY} was wrong")
        db.commit()

        listed = _say(f"/forget {CANARY}")
        assert "3 things" in listed and "message" in listed and "memory" in listed and "correction" in listed

        asked = _say("/forget delete all")
        assert "/forget yes" in asked
        assert CANARY.encode() in _raw_bytes(path)                      # nothing deleted before the yes

        done = _say("/forget yes")
        assert done.startswith("Deleted:") and "1 message" in done and "1 memory" in done
        assert CANARY.encode() not in _raw_bytes(path)                  # while the agent still runs
        assert "I don't remember" in _say(f"/forget {CANARY}")
        assert db.fetchone("SELECT COUNT(*) AS n FROM episodes")["n"] == 1   # the unrelated reply stays
    finally:
        db.close()
    assert CANARY.encode() not in _raw_bytes(path)                      # not in the file, not in the WAL


def test_n10_history_deletes_a_day_and_its_summaries_after_a_yes_and_keeps_other_days(tmp_path):
    from windyfly.commands.core import wire_runtime

    path = tmp_path / "history.db"
    db = Database(str(path))
    try:
        init_all_commands(db=db, config={})
        wire_runtime(db=db)
        save_episode(db, "user", f"on that day we spoke of {CANARY}", session_id="s1")
        save_episode(db, "user", "a message on another day", session_id="s1")
        upsert_node(db, "relationship_moment", f"moment: {CANARY} made them laugh")
        db.commit()
        db.execute("UPDATE episodes SET created_at = '2026-10-05 12:00:00' WHERE content LIKE ?", (f"%{CANARY}%",))
        db.execute("UPDATE episodes SET created_at = '2026-10-09 12:00:00' WHERE content LIKE 'a message%'")
        db.execute("UPDATE nodes SET created_at = '2026-10-05 13:00:00' WHERE type = 'relationship_moment'")
        db.commit()

        asked = _say("/history delete 2026-10-05")
        assert "1 message" in asked and "1 memory" in asked and "/history yes" in asked
        assert CANARY.encode() in _raw_bytes(path)                      # nothing deleted before the yes

        done = _say("/history yes")
        assert done.startswith("Deleted:")
        assert CANARY.encode() not in _raw_bytes(path)                  # while the agent still runs
        assert db.fetchone("SELECT COUNT(*) AS n FROM episodes")["n"] == 1   # the other day stays
    finally:
        db.close()
    assert CANARY.encode() not in _raw_bytes(path)
