"""Single-worker executor that keeps agent turns off the event loop.

``agent_respond`` is synchronous — blocking network I/O, up to ~11
sequential LLM calls on a bad turn — but every channel invokes it from
inside the asyncio event loop, so one long turn used to freeze
heartbeats, watchdog petting, typing indicators, /panic short-circuits,
and every other channel's traffic for its full duration (2026-07-04
audit, top structural weakness).

Turns run in ONE dedicated worker thread instead:

- the event loop stays free for everything above;
- ``max_workers=1`` preserves the existing turn-at-a-time execution
  model, so module-level state shared by turns (session token tracker,
  provider cooldowns, interaction counters) gains no new concurrency.

Raising the worker count is a deliberate future decision that requires
auditing that shared state first — don't bump it casually.

The goal-autorun/goal-pacing orchestrators already run their calls via
``run_in_executor`` with the default pool; main chat paths route here.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable

_turn_executor = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="agent-turn",
)


async def run_turn(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Await ``fn(*args, **kwargs)`` executed on the agent-turn thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _turn_executor, partial(fn, *args, **kwargs),
    )
