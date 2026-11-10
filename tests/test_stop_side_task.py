"""stop_side_task ends a helper task without hiding a cancel of the caller (Hub's LOW on #732).

The old pattern, `task.cancel(); try: await task; except CancelledError: pass`, swallowed the caller's OWN cancel
when it landed during that wait, so a shutdown went on as if nothing happened. Used by the Matrix and Telegram
typing signs.
"""
from __future__ import annotations

import asyncio

import pytest

from windyfly.channels.base import stop_side_task


async def _forever() -> None:
    await asyncio.Event().wait()


async def _stubborn() -> None:
    """A helper that takes a moment to finish after its cancel, so the caller can be cancelled meanwhile."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.sleep(0.05))
        raise


@pytest.mark.asyncio
async def test_the_helper_is_cancelled_and_awaited():
    task = asyncio.create_task(_forever())
    await asyncio.sleep(0)
    await stop_side_task(task)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_a_helper_that_already_failed_raises_nothing():
    async def boom() -> None:
        raise RuntimeError("send failed")
    task = asyncio.create_task(boom())
    await asyncio.sleep(0)
    await stop_side_task(task)          # no raise
    assert task.done()


@pytest.mark.asyncio
async def test_a_cancel_of_the_caller_during_the_wait_is_not_swallowed():
    after = []

    async def caller() -> None:
        helper = asyncio.create_task(_stubborn())
        await asyncio.sleep(0)
        try:
            pass
        finally:
            await stop_side_task(helper)
        after.append("went on")         # must not run: the caller was cancelled

    outer = asyncio.create_task(caller())
    await asyncio.sleep(0.01)           # the caller is now waiting inside stop_side_task
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer
    assert after == []
