"""Priority write queue for batching database writes.

Uses a daemon thread and PriorityQueue to decouple write operations
from the main agent loop. Three priority levels: HIGH=0, MEDIUM=1, LOW=2.
"""

from __future__ import annotations

import logging
import queue
import threading
from enum import IntEnum
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Process-wide write-failure telemetry (2026-07-04 audit): a full disk
# used to mean every episode save failed silently while the agent kept
# chatting — memory loss discovered weeks later. Any WriteQueue instance
# feeds these; /status reads them to surface "memory writes failing".
_write_stats: dict[str, Any] = {
    "failures": 0, "last_failure_ts": 0.0, "last_error": "",
    # Writes abandoned because stop() gave up waiting for the drain.
    # Kept separate from `failures`: nothing raised — the work simply
    # never ran. Silent memory loss is the one failure mode Principle
    # #7 cannot tolerate, so it gets its own counter.
    "shutdown_dropped": 0,
}
_write_stats_lock = threading.Lock()

# How long stop() waits for the worker to drain. Generous on purpose:
# with the [semantic] extra installed every save_episode embeds, and
# the first one pays a multi-second cold model load.
_STOP_JOIN_TIMEOUT = 30.0


def _note_write_failure(error: Exception) -> None:
    import time
    with _write_stats_lock:
        _write_stats["failures"] += 1
        _write_stats["last_failure_ts"] = time.time()
        _write_stats["last_error"] = f"{type(error).__name__}: {error}"[:200]


def _note_shutdown_drop(count: int) -> None:
    """Record writes abandoned by a timed-out stop()."""
    import time
    with _write_stats_lock:
        _write_stats["shutdown_dropped"] += max(count, 1)
        _write_stats["last_failure_ts"] = time.time()
        _write_stats["last_error"] = (
            f"stop() timed out with ~{count} write(s) queued"
        )


def get_write_stats() -> dict:
    """Snapshot of process-wide write-failure telemetry."""
    with _write_stats_lock:
        return dict(_write_stats)


def reset_write_stats() -> None:
    """Test hook — zero the process-wide counters."""
    with _write_stats_lock:
        _write_stats.update(
            {"failures": 0, "last_failure_ts": 0.0, "last_error": "",
             "shutdown_dropped": 0}
        )


class Priority(IntEnum):
    """Write queue priority levels."""
    HIGH = 0
    MEDIUM = 1
    LOW = 2


class WriteQueue:
    """Threaded priority queue for database write operations.

    HIGH priority items are processed immediately.
    MEDIUM priority items are batched (up to 50) and executed
    in a single transaction.
    LOW priority items are processed individually after all higher
    priority items.
    """

    def __init__(self) -> None:
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._counter = 0
        self._counter_lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def enqueue(
        self,
        priority: int | Priority,
        fn: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Add a write operation to the queue.

        Args:
            priority: Priority level (0=HIGH, 1=MEDIUM, 2=LOW).
            fn: Callable to execute.
            *args: Positional arguments for fn.
            **kwargs: Keyword arguments for fn.
        """
        with self._counter_lock:
            count = self._counter
            self._counter += 1
        self._queue.put((int(priority), count, fn, args, kwargs))

    def _worker(self) -> None:
        """Background worker that processes queued writes."""
        medium_batch: list[tuple[Callable, tuple, dict]] = []

        while self._running or not self._queue.empty():
            try:
                priority, _count, fn, args, kwargs = self._queue.get(timeout=0.5)
            except queue.Empty:
                # Flush any pending medium batch
                if medium_batch:
                    self._flush_batch(medium_batch)
                    medium_batch.clear()
                continue

            try:
                if priority == Priority.HIGH:
                    # Flush pending medium batch first
                    if medium_batch:
                        self._flush_batch(medium_batch)
                        medium_batch.clear()
                    fn(*args, **kwargs)

                elif priority == Priority.MEDIUM:
                    medium_batch.append((fn, args, kwargs))
                    if len(medium_batch) >= 50:
                        self._flush_batch(medium_batch)
                        medium_batch.clear()

                else:  # LOW
                    # Flush pending medium batch first
                    if medium_batch:
                        self._flush_batch(medium_batch)
                        medium_batch.clear()
                    fn(*args, **kwargs)

            except Exception as e:
                logger.exception("WriteQueue: error processing item (priority=%s): %s", priority, e)
                _note_write_failure(e)
            finally:
                self._queue.task_done()

        # Final flush on shutdown
        if medium_batch:
            self._flush_batch(medium_batch)

    @staticmethod
    def _flush_batch(batch: list[tuple[Callable, tuple, dict]]) -> None:
        """Execute a batch of medium-priority operations."""
        for fn, args, kwargs in batch:
            try:
                fn(*args, **kwargs)
            except Exception as e:
                logger.exception("WriteQueue: error in batch item: %s", e)
                _note_write_failure(e)

    def start(self) -> None:
        """Start the background worker thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = _STOP_JOIN_TIMEOUT) -> None:
        """Signal the worker to stop and wait for it to drain.

        The worker loop is ``while self._running or not
        self._queue.empty()``, so it finishes what is queued before
        exiting — PROVIDED we actually wait for it.

        **A timed-out join here is silent memory loss.** The old
        hardcoded 5s was tuned for bare SQLite inserts. Enabling the
        ``[semantic]`` extra puts an embedding compute inside every
        ``save_episode``, and the first one pays a multi-second cold
        model load; 20 queued episodes then blow straight past 5s, the
        join gives up, and the turns are gone. Caught exactly that way:
        ``test_stop_flushes_pending_items`` expected 20 episodes and
        found 0, with "Loading weights" in the captured stderr.

        In production that is grandma's last few turns of conversation
        disappearing on a graceful shutdown — a Principle-#7 failure
        that the old code could not even report, because ``join``
        returns None whether it drained or gave up.

        So: a longer default, and if it still times out we say so
        loudly and record it in the process-wide write telemetry that
        ``/status`` already surfaces. Losing memory may sometimes be
        unavoidable; losing it *quietly* is not acceptable.
        """
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                pending = self._queue.qsize()
                logger.error(
                    "WriteQueue.stop() timed out after %.1fs with ~%d write(s) "
                    "still queued — memory for those turns is LOST",
                    timeout, pending,
                )
                _note_shutdown_drop(pending)
            self._thread = None
