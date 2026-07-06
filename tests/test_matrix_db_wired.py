"""Regression: the matrix channel must inject the live DB into the command
registry, or every command gated on the module-level `_db` returns
"Database not available".

Surfaced 2026-07-06 by a live Windy Chat command sweep: /budget, /tokens,
/sliders, /facts, /history, /intents all replied "Database not available"
while /status and /passport worked — because the matrix branch of main()
never called wire_runtime(db=...) (telegram + generic channels do).
"""

from __future__ import annotations

import pytest

from windyfly.channels.base import handle_incoming
from windyfly.commands.setup import init_all_commands
from windyfly.memory.database import Database


DB_GATED = ["/budget", "/tokens", "/sliders", "/facts", "/history", "/intents"]


@pytest.mark.asyncio
async def test_db_gated_commands_fail_without_wire():
    """Baseline: with config-only init (what main() does early, before the
    channel branch), the db-gated commands report the failure. This is the
    state the matrix branch used to ship."""
    init_all_commands(config={})  # no db, no wire_runtime
    saw_failure = False
    for cmd in DB_GATED:
        ok, out = await handle_incoming(cmd, {"platform": "matrix", "channel_id": "x"})
        if "Database not available" in out:
            saw_failure = True
    assert saw_failure, "expected at least one db-gated command to fail pre-wire"


@pytest.mark.asyncio
async def test_db_gated_commands_work_after_wire():
    """After wire_runtime(db=...) — what the matrix branch now does — none of
    the db-gated commands should report "Database not available"."""
    from windyfly.commands.core import wire_runtime

    db = Database(":memory:")
    init_all_commands(db=db, config={})
    wire_runtime(db=db)
    try:
        for cmd in DB_GATED:
            ok, out = await handle_incoming(
                cmd, {"platform": "matrix", "channel_id": "x"}
            )
            assert ok is True, f"{cmd} not handled"
            assert "Database not available" not in out, (
                f"{cmd} still reports no DB after wire_runtime"
            )
    finally:
        db.close()


def test_matrix_branch_wires_runtime():
    """Belt-and-suspenders: the matrix branch of main() must call
    wire_runtime so the fix can't silently regress."""
    import inspect
    from windyfly import main as main_mod

    src = inspect.getsource(main_mod.main)
    matrix_seg = src.split('== "matrix"', 1)[1].split('== "telegram"', 1)[0]
    assert "wire_runtime(db=db)" in matrix_seg
