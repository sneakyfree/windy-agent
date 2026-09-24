"""Per-reply timing: where a turn's wall-clock goes, one log line per turn.

    [req:ab12cd34] timing total=3.21s queued=0.00s prompt=0.41s (memory_search=0.30s
      embed=0.02s embed_wait=0.00s) llm=2.60s×1 tools=0.00s×0 other=0.20s

Phases nest (``memory_search`` runs inside ``prompt``; ``embed`` inside
``memory_search``), so only the top-level ones (prompt, llm*, tools) are
subtracted to get ``other``. ``queued`` is the wait for the single agent-turn
thread before the turn started (another turn, the journey probe, a helper).

Durations only: no content ever enters this module. Timing must never break a
reply, so every entry point swallows its own errors.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar, cast

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Top-level phases; everything else is a breakdown of one of these.
_TOP = ("prompt", "tools")
_TOP_PREFIX = "llm"


class TurnTiming:
    def __init__(self, queued_s: float = 0.0) -> None:
        self.t0 = time.monotonic()
        self.queued_s = queued_s
        self.phases: dict[str, list[float]] = {}  # name -> [seconds, count]

    def add(self, name: str, seconds: float) -> None:
        cur = self.phases.setdefault(name, [0.0, 0])
        cur[0] += seconds
        cur[1] += 1

    def summary(self) -> dict[str, object]:
        total = time.monotonic() - self.t0
        top = sum(v[0] for k, v in self.phases.items() if k in _TOP or k.startswith(_TOP_PREFIX))
        out: dict[str, object] = {"total_s": round(total, 3), "queued_s": round(self.queued_s, 3),
                                  "other_s": round(max(0.0, total - top), 3)}
        for k, (s, n) in sorted(self.phases.items()):
            out[f"{k}_s"] = round(s, 3)
            out[f"{k}_n"] = n
        return out

    def line(self) -> str:
        total = time.monotonic() - self.t0

        def s(name: str) -> str:
            v = self.phases.get(name)
            return f"{v[0]:.2f}s" if v else "0.00s"

        def sn(name: str) -> str:
            v = self.phases.get(name)
            return f"{v[0]:.2f}s×{v[1]}" if v else "0.00s×0"

        llm_extra = " ".join(f"{k}={sn(k)}" for k in sorted(self.phases)
                             if k.startswith("llm:"))
        top = sum(v[0] for k, v in self.phases.items() if k in _TOP or k.startswith(_TOP_PREFIX))
        return (f"timing total={total:.2f}s queued={self.queued_s:.2f}s prompt={s('prompt')} "
                f"(memory_search={s('memory_search')} embed={s('embed')} embed_wait={s('embed_wait')}) "
                f"llm={sn('llm')}{(' ' + llm_extra) if llm_extra else ''} tools={sn('tools')} "
                f"other={max(0.0, total - top):.2f}s")


_CURRENT: contextvars.ContextVar[TurnTiming | None] = contextvars.ContextVar("turn_timing", default=None)
# run_in_executor does not copy contextvars, so the executor hands the queue
# wait to the turn thread through a thread-local that start() consumes.
_PENDING = threading.local()


def note_queued(seconds: float) -> None:
    _PENDING.queued_s = seconds


def start() -> contextvars.Token | None:
    try:
        queued = getattr(_PENDING, "queued_s", 0.0) or 0.0
        _PENDING.queued_s = 0.0
        return _CURRENT.set(TurnTiming(queued))
    except Exception:  # noqa: BLE001
        return None


def finish(token: contextvars.Token | None, req_id: str = "") -> dict[str, object] | None:
    """Log the turn's timing line and return its summary (None if not started)."""
    try:
        cur = _CURRENT.get()
        if cur is None:
            return None
        logger.info("[req:%s] %s", req_id, cur.line())
        return cur.summary()
    except Exception:  # noqa: BLE001
        return None
    finally:
        if token is not None:
            try:
                _CURRENT.reset(token)
            except Exception:  # noqa: BLE001
                _CURRENT.set(None)


def current() -> TurnTiming | None:
    return _CURRENT.get()


@contextmanager
def phase(name: str) -> Iterator[None]:
    """Time a block into the current turn; a no-op outside a turn."""
    cur = _CURRENT.get()
    if cur is None:
        yield
        return
    t = time.monotonic()
    try:
        yield
    finally:
        try:
            cur.add(name, time.monotonic() - t)
        except Exception:  # noqa: BLE001
            pass


def timed(name: str) -> Callable[[F], F]:
    """Decorator form of :func:`phase` for a fixed phase name."""
    import functools

    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with phase(name):
                return fn(*args, **kwargs)
        return cast(F, wrapper)
    return deco
