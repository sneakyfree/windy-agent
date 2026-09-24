"""Per-reply timing (observability/turn_timing): phases, nesting, queue wait, log line."""

from __future__ import annotations

import asyncio
import logging
import time

from windyfly.agent import executor
from windyfly.observability import turn_timing as tt


def test_phase_is_noop_outside_a_turn():
    with tt.phase("llm"):
        pass
    assert tt.current() is None


def test_phases_nest_and_other_excludes_only_top_level(caplog):
    caplog.set_level(logging.INFO, logger="windyfly.observability.turn_timing")
    tok = tt.start()
    with tt.phase("prompt"):
        with tt.phase("memory_search"):
            with tt.phase("embed"):
                time.sleep(0.02)
    for _ in range(2):
        with tt.phase("llm"):
            time.sleep(0.01)
    with tt.phase("llm:facts"):
        pass
    with tt.phase("tools"):
        pass
    s = tt.finish(tok, "abc123")
    assert s is not None and tt.current() is None
    assert s["llm_n"] == 2 and s["llm:facts_n"] == 1 and s["tools_n"] == 1
    assert s["memory_search_s"] <= s["prompt_s"]
    top = s["prompt_s"] + s["llm_s"] + s["llm:facts_s"] + s["tools_s"]
    assert abs(s["total_s"] - top - s["other_s"]) < 0.01  # nested phases not double-counted
    (line,) = [r.getMessage() for r in caplog.records]
    assert line.startswith("[req:abc123] timing total=") and "llm=0.0" in line and "×2" in line
    assert "llm:facts=" in line and "memory_search=" in line


def test_finish_without_start_is_none():
    assert tt.finish(None) is None


def test_decorator_records_and_passes_through():
    @tt.timed("memory_search")
    def f(x):
        return x * 2

    tok = tt.start()
    assert f(21) == 42
    s = tt.finish(tok)
    assert s["memory_search_n"] == 1


def test_executor_reports_queue_wait():
    seen = {}

    def turn():
        tok = tt.start()
        seen.update(tt.finish(tok) or {})

    def busy():
        time.sleep(0.15)

    async def go():
        await asyncio.gather(executor.run_turn(busy), executor.run_turn(turn))

    asyncio.run(go())
    assert seen["queued_s"] >= 0.1  # waited for the single agent-turn thread
