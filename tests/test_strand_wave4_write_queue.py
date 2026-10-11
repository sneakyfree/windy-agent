"""Strand-to-green Wave 4, C0.11: write-queue strict order HIGH > MEDIUM > LOW.

``WriteQueue`` documents three priorities (HIGH=0, MEDIUM=1, LOW=2) with
FIFO order inside a priority (the enqueue counter is the tie-breaker).
These tests pin the observable order with several items of each level.
"""

from __future__ import annotations

import threading

from windyfly.memory.write_queue import Priority, WriteQueue


def _recorder():
    order: list[str] = []
    lock = threading.Lock()

    def record(label: str) -> None:
        with lock:
            order.append(label)

    return order, record


def test_C0_11_strict_priority_order_with_several_items_each():
    wq = WriteQueue()
    order, record = _recorder()

    # Interleaved on purpose: arrival order says nothing about priority.
    plan = [
        (Priority.LOW, "L1"), (Priority.MEDIUM, "M1"), (Priority.HIGH, "H1"),
        (Priority.LOW, "L2"), (Priority.MEDIUM, "M2"), (Priority.HIGH, "H2"),
        (Priority.MEDIUM, "M3"), (Priority.LOW, "L3"), (Priority.HIGH, "H3"),
        (Priority.HIGH, "H4"), (Priority.LOW, "L4"), (Priority.MEDIUM, "M4"),
    ]
    for prio, label in plan:
        wq.enqueue(prio, record, label)

    # Everything is queued before the worker starts, so the worker sees
    # the whole backlog and must drain it strictly by priority.
    wq.start()
    wq.stop(timeout=10)

    assert order == [
        "H1", "H2", "H3", "H4",
        "M1", "M2", "M3", "M4",
        "L1", "L2", "L3", "L4",
    ]


def test_C0_11_plain_int_priorities_order_the_same_way():
    """``enqueue`` accepts bare ints (0/1/2) as well as the enum."""
    wq = WriteQueue()
    order, record = _recorder()
    for prio, label in [(2, "low-a"), (1, "med-a"), (0, "high-a"),
                        (2, "low-b"), (0, "high-b"), (1, "med-b")]:
        wq.enqueue(prio, record, label)
    wq.start()
    wq.stop(timeout=10)
    assert order == ["high-a", "high-b", "med-a", "med-b", "low-a", "low-b"]


def test_C0_11_late_high_jumps_ahead_of_waiting_low_items():
    """While the worker is busy, a HIGH write enqueued AFTER several LOW
    and MEDIUM writes still runs before all of them."""
    wq = WriteQueue()
    order, record = _recorder()
    started = threading.Event()
    release = threading.Event()

    def blocker() -> None:
        started.set()
        release.wait(timeout=10)
        record("blocker")

    wq.enqueue(Priority.HIGH, blocker)
    wq.start()
    try:
        assert started.wait(timeout=5), "worker never picked up the first item"
        # Worker is now stuck inside blocker(); build a backlog behind it.
        wq.enqueue(Priority.LOW, record, "L1")
        wq.enqueue(Priority.LOW, record, "L2")
        wq.enqueue(Priority.MEDIUM, record, "M1")
        wq.enqueue(Priority.MEDIUM, record, "M2")
        wq.enqueue(Priority.HIGH, record, "H-late-1")
        wq.enqueue(Priority.HIGH, record, "H-late-2")
    finally:
        release.set()
        wq.stop(timeout=10)

    assert order == ["blocker", "H-late-1", "H-late-2", "M1", "M2", "L1", "L2"]


def test_C0_11_a_failing_write_does_not_reorder_or_drop_the_rest():
    from windyfly.memory.write_queue import reset_write_stats

    wq = WriteQueue()
    order, record = _recorder()

    def boom() -> None:
        record("boom")
        raise RuntimeError("disk full (simulated)")

    wq.enqueue(Priority.LOW, record, "L1")
    wq.enqueue(Priority.HIGH, boom)
    wq.enqueue(Priority.MEDIUM, record, "M1")
    wq.enqueue(Priority.HIGH, record, "H2")
    wq.start()
    wq.stop(timeout=10)
    reset_write_stats()  # the failure is process-wide telemetry; leave it clean

    assert order == ["boom", "H2", "M1", "L1"]
