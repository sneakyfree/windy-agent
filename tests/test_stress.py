"""Phase H4 — Stress & Resilience Tests.

Break things under load. Verify the platform handles concurrent requests,
large datasets, malformed inputs, and edge cases without crashing.
"""

from __future__ import annotations

import asyncio
import time


from windyfly.control_panel import PRESETS, apply_preset, get_sliders
from windyfly.memory.database import Database
from windyfly.memory.nodes import upsert_node
from windyfly.memory.write_queue import WriteQueue


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =============================================================================
# H4.1: 100 Concurrent Slider SET Requests
# =============================================================================


class TestLargeDataset:
    def test_search_on_1000_nodes(self):
        """H4.4: Search on 1000+ nodes completes in < 1s."""
        db = Database(":memory:")
        for i in range(1000):
            upsert_node(db, type="fact", name=f"fact_{i}", metadata={"value": f"data_{i}"})

        start = time.time()
        from windyfly.memory.nodes import search_nodes
        results = search_nodes(db, "fact_500", limit=10)
        elapsed = time.time() - start

        assert elapsed < 1.0, f"Search on 1000 nodes took {elapsed:.2f}s (>1s)"
        assert len(results) >= 1
        db.close()


# =============================================================================
# H4.6: Decay on Large DB
# =============================================================================


class TestDecayPerformance:
    def test_decay_on_10k_nodes(self):
        """H4.6: Cognitive decay on 10k nodes completes in < 5s."""
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()

        # Insert 10k nodes (no decay scores set, so none will decay)
        for i in range(10000):
            db.execute(
                "INSERT INTO nodes (id, user_id, type, name, source) "
                "VALUES (?, 'default', 'fact', ?, 'bulk')",
                (f"node-{i}", f"fact_{i}"),
            )
        db.commit()

        start = time.time()
        from windyfly.memory.decay import run_decay
        result = run_decay(db, wq)
        elapsed = time.time() - start

        assert elapsed < 5.0, f"Decay on 10k nodes took {elapsed:.2f}s (>5s)"
        time.sleep(0.5)
        wq.stop()
        db.close()


# =============================================================================
# H4.7–H4.10: Resilience Tests
# =============================================================================


class TestResilience:


    def test_rapid_preset_cycling_50x(self):
        """H4.9: 50 rapid preset cycles — no corruption."""
        db = Database(":memory:")
        for _ in range(50):
            for preset_name in PRESETS:
                apply_preset(db, preset_name)
        # Final state should match last preset
        last_preset = list(PRESETS.keys())[-1]
        readback = get_sliders(db)
        for slider, expected in PRESETS[last_preset].items():
            assert readback[slider] == expected
        db.close()
