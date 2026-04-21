"""End-to-end tests for the capability audit ledger (Wave 2 #2).

Verifies that registering the audit hooks on a CapabilityRegistry causes
each invoke() to land a row in the agent_actions table — start row from
pre-invoke, end row from post-invoke, with success/failure properly
captured.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path

import pytest

from windyfly.agent.capabilities import (
    Band,
    Capability,
    CapabilityRegistry,
    Tier,
    install_audit_hooks,
)
from windyfly.memory.agent_actions import (
    capability_success_rate,
    get_actions_for_capability,
    get_failed_actions,
    get_recent_actions,
)
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


@pytest.fixture
def db_and_wq():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        db = Database(db_path)
        wq = WriteQueue()
        wq.start()
        try:
            yield db, wq
        finally:
            wq.stop()
            db.close()


def _drain(wq: WriteQueue, timeout: float = 2.0) -> None:
    """Wait until the write queue is empty so assertions see the writes."""
    start = time.time()
    while not wq._queue.empty():
        if time.time() - start > timeout:
            raise TimeoutError("write queue did not drain")
        time.sleep(0.05)
    # Give the worker a beat to commit the in-flight item it pulled
    # right before the queue went empty.
    time.sleep(0.1)


@pytest.mark.asyncio
async def test_audit_writes_start_and_end_rows(db_and_wq):
    db, wq = db_and_wq
    registry = CapabilityRegistry()
    install_audit_hooks(registry, db, wq)

    registry.register(Capability(
        id="test.audited",
        description="audited test capability",
        handler=lambda x="default": f"ok-{x}",
        tier=Tier.READ_EXTERNAL,  # audit_required=True by default
    ))

    out = await registry.invoke("test.audited", {"x": "hi"}, Band.OWNER)
    assert out == "ok-hi"

    _drain(wq)
    rows = get_actions_for_capability(db, "test.audited")
    assert len(rows) == 1
    row = rows[0]
    assert row["capability_id"] == "test.audited"
    assert row["band"] == "OWNER"
    assert row["success"] == 1
    assert row["error_class"] is None
    assert row["ended_at"] is not None
    assert row["started_at"] is not None
    assert row["duration_ms"] is not None and row["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_audit_records_failure(db_and_wq):
    db, wq = db_and_wq
    registry = CapabilityRegistry()
    install_audit_hooks(registry, db, wq)

    def boom():
        raise RuntimeError("intentional test failure")

    registry.register(Capability(
        id="test.fails",
        description="failing capability",
        handler=boom,
        tier=Tier.READ_EXTERNAL,
    ))

    with pytest.raises(RuntimeError, match="intentional"):
        await registry.invoke("test.fails", {}, Band.OWNER)

    _drain(wq)
    rows = get_failed_actions(db)
    assert len(rows) == 1
    assert rows[0]["capability_id"] == "test.fails"
    assert rows[0]["success"] == 0
    assert rows[0]["error_class"] == "RuntimeError"
    assert "intentional" in rows[0]["error_message"]


@pytest.mark.asyncio
async def test_pure_compute_skips_audit(db_and_wq):
    """Tier 0 capabilities have audit_required=False by default.
    Calling them shouldn't fill the ledger with /dice/roll noise."""
    db, wq = db_and_wq
    registry = CapabilityRegistry()
    install_audit_hooks(registry, db, wq)

    registry.register(Capability(
        id="dice.roll",
        description="roll a die",
        handler=lambda: 4,
        tier=Tier.PURE_COMPUTE,  # audit_required=False
    ))

    for _ in range(5):
        await registry.invoke("dice.roll", {}, Band.OWNER)

    _drain(wq)
    rows = get_recent_actions(db)
    assert rows == []  # nothing written


@pytest.mark.asyncio
async def test_args_redacted_before_storage(db_and_wq):
    """Tool args may contain secrets — they must be redacted before
    landing in the ledger or we've leaked credentials to disk."""
    db, wq = db_and_wq
    registry = CapabilityRegistry()
    install_audit_hooks(registry, db, wq)

    registry.register(Capability(
        id="test.with_secrets",
        description="capability with sensitive args",
        handler=lambda token="x": "ok",
        tier=Tier.READ_EXTERNAL,
    ))

    await registry.invoke(
        "test.with_secrets",
        {"token": "sk-proj-NotARealKeyButLongEnoughToMatch"},
        Band.OWNER,
    )

    _drain(wq)
    rows = get_actions_for_capability(db, "test.with_secrets")
    assert len(rows) == 1
    args_json = rows[0]["args_json"]
    assert "NotARealKeyButLongEnoughToMatch" not in args_json
    assert "***REDACTED***" in args_json


@pytest.mark.asyncio
async def test_concurrent_invocations_dont_cross_contaminate(db_and_wq):
    """Contextvar correlation must keep parallel invokes' action ids
    separate — pre and post for invocation A must not bleed into
    the row for invocation B."""
    db, wq = db_and_wq
    registry = CapabilityRegistry()
    install_audit_hooks(registry, db, wq)

    async def slow_handler(label="x"):
        await asyncio.sleep(0.05)
        return label

    registry.register(Capability(
        id="test.slow",
        description="async slow capability",
        handler=slow_handler,
        tier=Tier.READ_EXTERNAL,
    ))

    results = await asyncio.gather(*(
        registry.invoke("test.slow", {"label": f"call-{i}"}, Band.OWNER)
        for i in range(5)
    ))
    assert results == [f"call-{i}" for i in range(5)]

    _drain(wq)
    rows = get_actions_for_capability(db, "test.slow")
    assert len(rows) == 5
    # Every row should be successfully closed
    assert all(r["success"] == 1 for r in rows)
    assert all(r["ended_at"] is not None for r in rows)
    # Each got its own unique action id
    assert len({r["id"] for r in rows}) == 5


@pytest.mark.asyncio
async def test_install_audit_hooks_is_idempotent(db_and_wq):
    db, wq = db_and_wq
    registry = CapabilityRegistry()
    install_audit_hooks(registry, db, wq)
    install_audit_hooks(registry, db, wq)
    install_audit_hooks(registry, db, wq)

    registry.register(Capability(
        id="test.once",
        description="should only audit once",
        handler=lambda: "ok",
        tier=Tier.READ_EXTERNAL,
    ))

    await registry.invoke("test.once", {}, Band.OWNER)
    _drain(wq)
    rows = get_actions_for_capability(db, "test.once")
    assert len(rows) == 1  # not 3


@pytest.mark.asyncio
async def test_session_id_provider_threaded_through(db_and_wq):
    db, wq = db_and_wq
    registry = CapabilityRegistry()
    install_audit_hooks(
        registry, db, wq,
        session_id_provider=lambda: "telegram:8545546994",
    )

    registry.register(Capability(
        id="test.with_session",
        description="x",
        handler=lambda: "ok",
        tier=Tier.READ_EXTERNAL,
    ))

    await registry.invoke("test.with_session", {}, Band.OWNER)
    _drain(wq)
    rows = get_actions_for_capability(db, "test.with_session")
    assert rows[0]["session_id"] == "telegram:8545546994"


@pytest.mark.asyncio
async def test_capability_success_rate_query(db_and_wq):
    db, wq = db_and_wq
    registry = CapabilityRegistry()
    install_audit_hooks(registry, db, wq)

    call_count = [0]

    def flaky():
        call_count[0] += 1
        if call_count[0] % 2 == 0:
            raise RuntimeError("every other one fails")
        return "ok"

    registry.register(Capability(
        id="test.flaky",
        description="x",
        handler=flaky,
        tier=Tier.READ_EXTERNAL,
    ))

    for _ in range(4):
        try:
            await registry.invoke("test.flaky", {}, Band.OWNER)
        except RuntimeError:
            pass

    _drain(wq)
    stats = capability_success_rate(db, "test.flaky")
    assert stats["total"] == 4
    assert stats["successes"] == 2
    assert stats["success_rate"] == 0.5


@pytest.mark.asyncio
async def test_args_json_is_valid_json(db_and_wq):
    db, wq = db_and_wq
    registry = CapabilityRegistry()
    install_audit_hooks(registry, db, wq)

    registry.register(Capability(
        id="test.json_args",
        description="x",
        handler=lambda **kw: "ok",
        tier=Tier.READ_EXTERNAL,
    ))

    await registry.invoke(
        "test.json_args",
        {"a": 1, "b": "two", "c": [1, 2, 3]},
        Band.OWNER,
    )
    _drain(wq)
    rows = get_actions_for_capability(db, "test.json_args")
    parsed = json.loads(rows[0]["args_json"])
    assert parsed == {"a": 1, "b": "two", "c": [1, 2, 3]}
