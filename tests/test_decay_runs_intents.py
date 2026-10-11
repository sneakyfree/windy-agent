"""/decay and the dashboard's decay.run decay intents too, like the daily job (one cycle: run_decay)."""

from __future__ import annotations

import asyncio
import time

from windyfly.memory.database import Database
from windyfly.memory.intents import create_intent, get_intent


def _stale_and_fresh(db: Database) -> tuple[str, str]:
    stale = create_intent(db, "Old forgotten goal")
    fresh = create_intent(db, "Fresh goal")
    db.execute(
        "UPDATE intents SET decay_score = 0.31, last_touched = datetime('now', '-30 days') WHERE id = ?",
        (stale,),
    )
    db.commit()
    return stale, fresh


def _status(db: Database, intent_id: str) -> str:
    row = get_intent(db, intent_id)
    assert row is not None
    return row["status"]


def test_slash_decay_pauses_stale_intents():
    from windyfly.commands import core
    from windyfly.commands.registry import parse_command, registry

    db = Database(":memory:")
    stale, fresh = _stale_and_fresh(db)
    core.init_core(db=db, config={})
    reply = asyncio.run(registry.execute(parse_command("/decay"), {"platform": "cli"}))
    assert not reply.startswith("Error")
    assert _status(db, stale) == "paused"
    assert _status(db, fresh) == "active"
    db.close()


def test_uds_decay_run_pauses_stale_intents():
    from tests.test_gap_closure import _make_bridge, _run

    bridge, db, wq = _make_bridge()
    wq.start()
    try:
        stale, fresh = _stale_and_fresh(db)
        _run(bridge._dispatch("decay.run", {}))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _status(db, stale) != "paused":
            time.sleep(0.05)
    finally:
        wq.stop()
    assert _status(db, stale) == "paused"
    assert _status(db, fresh) == "active"
    db.close()
