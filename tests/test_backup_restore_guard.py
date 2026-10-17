"""/backup restore is switched off until the safe restore lands (Hub, 10-17; specs/AGENT_RESTORE_REVIEW_P4_11_16).

The old restore wrote the backup over the memory file while the agent held it open in WAL mode, with
no yes, and left a plaintext copy beside it. Until the rebuild, the command refuses and touches nothing.
"""

from __future__ import annotations

import asyncio

from windyfly.channels.base import handle_incoming
from windyfly.commands.core import wire_runtime
from windyfly.commands.setup import init_all_commands
from windyfly.memory.database import Database


def test_backup_restore_refuses_and_touches_nothing(tmp_path, monkeypatch):
    import windyfly.cloud_backup as cb

    called = []

    async def must_not_run(*a, **k):
        called.append(a)
        return {"success": True}

    monkeypatch.setattr(cb, "restore_from_cloud", must_not_run)
    path = tmp_path / "memory.db"
    db = Database(str(path))
    try:
        init_all_commands(db=db, config={})
        wire_runtime(db=db)
        db.execute("INSERT INTO soul (id, user_id, key, value) VALUES ('k', 'default', 'canary', 'today')")
        db.commit()
        before = sorted(p.name for p in tmp_path.iterdir())
        for text in ("/backup restore", "/backup restore latest", "/backup restore 2026-10-01.enc"):
            ok, out = asyncio.run(handle_incoming(text, {"platform": "cli", "channel_id": "guard"}))
            assert ok and "switched off" in out and "Nothing was changed" in out
        assert called == []
        assert db.fetchone("SELECT value FROM soul WHERE key = 'canary'")["value"] == "today"
        assert sorted(p.name for p in tmp_path.iterdir()) == before          # no .pre-restore copy
    finally:
        db.close()
