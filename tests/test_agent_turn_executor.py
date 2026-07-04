"""Agent-turn executor contract (2026-07-04 audit, Sprint 2).

agent_respond blocks for the full turn (up to ~11 sequential network
calls); run_turn moves it onto a dedicated single worker thread so the
event loop keeps servicing heartbeats, /panic short-circuits, and other
channels while a turn is in flight.
"""

from __future__ import annotations

import asyncio
import threading
import time

from windyfly.agent.executor import run_turn


def test_run_turn_returns_result_and_uses_worker_thread():
    seen = {}

    def blocking_fn(a, b, *, kw=None):
        seen["thread"] = threading.current_thread().name
        return (a, b, kw)

    result = asyncio.run(run_turn(blocking_fn, 1, 2, kw="x"))
    assert result == (1, 2, "x")
    assert seen["thread"].startswith("agent-turn")


def test_run_turn_propagates_exceptions():
    def boom():
        raise ValueError("turn failed")

    async def _go():
        try:
            await run_turn(boom)
        except ValueError as e:
            return str(e)
        return None

    assert asyncio.run(_go()) == "turn failed"


def test_event_loop_stays_responsive_during_blocking_turn():
    """The whole point: while a turn blocks its worker thread, the
    event loop must keep running other coroutines."""
    ticks = []

    def slow_turn():
        time.sleep(0.5)
        return "done"

    async def ticker():
        for _ in range(5):
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    async def _go():
        turn = asyncio.create_task(run_turn(slow_turn))
        tick = asyncio.create_task(ticker())
        result = await turn
        await tick
        return result

    assert asyncio.run(_go()) == "done"
    # The ticker must have made progress DURING the 0.5s blocking turn —
    # pre-fix (sync call on the loop) it could only start afterwards.
    assert len(ticks) == 5
    spread = ticks[-1] - ticks[0]
    assert spread >= 0.15, f"ticker was starved (spread={spread:.3f}s)"


def test_turns_are_serialized_single_worker():
    """max_workers=1 is a load-bearing invariant: module state shared
    across turns (session tokens, cooldowns) assumes turn-at-a-time."""
    active = []
    overlap = []

    def turn(i):
        active.append(i)
        if len(active) > 1:
            overlap.append(tuple(active))
        time.sleep(0.05)
        active.remove(i)
        return i

    async def _go():
        return await asyncio.gather(*(run_turn(turn, i) for i in range(4)))

    results = asyncio.run(_go())
    assert sorted(results) == [0, 1, 2, 3]
    assert not overlap, f"turns overlapped: {overlap}"
