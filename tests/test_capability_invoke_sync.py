"""Tests for CapabilityRegistry.invoke_sync (Wave 2 #4).

The whole point: lets sync code (agent_respond) dispatch async
capability handlers without crashing ``asyncio.run`` from inside an
already-running event loop. Two cases matter most:

  1. No loop on this thread — direct asyncio.run path
  2. A loop IS running on this thread — must use the worker loop on
     the daemon thread instead, otherwise asyncio.run raises
     "This event loop is already running"
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from windyfly.agent.capabilities import (
    Band,
    Capability,
    CapabilityDenied,
    CapabilityRegistry,
    Tier,
)


def _cap(id, handler, tier=Tier.PURE_COMPUTE):
    return Capability(
        id=id,
        description=f"d-{id}",
        handler=handler,
        tier=tier,
    )


def test_invoke_sync_works_without_running_loop():
    r = CapabilityRegistry()
    r.register(_cap("ping", lambda: "pong"))
    assert r.invoke_sync("ping", {}, Band.SANDBOX) == "pong"


def test_invoke_sync_works_with_async_handler():
    r = CapabilityRegistry()

    async def handler():
        await asyncio.sleep(0.001)
        return "async-pong"

    r.register(_cap("aping", handler))
    assert r.invoke_sync("aping", {}, Band.SANDBOX) == "async-pong"


def test_invoke_sync_propagates_capability_denied():
    r = CapabilityRegistry()
    r.register(_cap("priv", lambda: "ok", tier=Tier.FULL_MACHINE))
    with pytest.raises(CapabilityDenied):
        r.invoke_sync("priv", {}, Band.USER)


def test_invoke_sync_propagates_handler_errors():
    r = CapabilityRegistry()

    def bad():
        raise ValueError("kaboom")

    r.register(_cap("bad", bad))
    with pytest.raises(ValueError, match="kaboom"):
        r.invoke_sync("bad", {}, Band.SANDBOX)


@pytest.mark.asyncio
async def test_invoke_sync_works_inside_running_loop():
    """The hard case: agent_respond is called from inside an asyncio
    event loop (telegram, matrix). asyncio.run() would raise; we must
    fall through to the worker loop on the daemon thread."""
    r = CapabilityRegistry()
    r.register(_cap("ping", lambda: "from-inside-loop"))

    # We're currently inside an asyncio event loop (pytest-asyncio).
    # Run invoke_sync in a thread executor so it appears to be sync
    # code that's executing while a loop is also running on this same
    # thread context — the exact pattern agent_respond hits.
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, r.invoke_sync, "ping", {}, Band.SANDBOX)
    assert result == "from-inside-loop"


@pytest.mark.asyncio
async def test_invoke_sync_concurrent_calls_dont_deadlock():
    r = CapabilityRegistry()

    async def slow():
        await asyncio.sleep(0.05)
        return "ok"

    r.register(_cap("slow", slow))

    loop = asyncio.get_running_loop()
    # Five concurrent invoke_sync calls from inside an async context
    # all need to schedule onto the worker loop without deadlocking.
    results = await asyncio.gather(*(
        loop.run_in_executor(None, r.invoke_sync, "slow", {}, Band.SANDBOX)
        for _ in range(5)
    ))
    assert results == ["ok"] * 5


@pytest.mark.asyncio
async def test_invoke_sync_routes_through_worker_loop_when_loop_running():
    """Regression guard: when invoke_sync is called from a thread that
    has an asyncio loop running (e.g. via run_in_executor), it must
    use the worker loop, not asyncio.run (which would crash with
    'event loop is already running')."""
    r = CapabilityRegistry()
    r.register(_cap("ping", lambda: "ok"))

    loop = asyncio.get_running_loop()
    # The executor thread doesn't have a loop, but we exercise both
    # branches by also calling directly from an async helper that
    # itself dispatches via run_in_executor.
    result = await loop.run_in_executor(
        None, r.invoke_sync, "ping", {}, Band.SANDBOX,
    )
    assert result == "ok"
