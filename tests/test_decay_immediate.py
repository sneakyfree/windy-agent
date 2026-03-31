"""Test that run_decay returns accurate counts.

R1.7: Validates that decay runs synchronously and returns
real counts (not deferred zeros).
"""

from windyfly.memory.database import Database
from windyfly.memory.decay import run_decay
from windyfly.memory.write_queue import WriteQueue


class TestDecayImmediate:
    """Tests that decay executes synchronously and returns real results."""

    def test_returns_nonzero_counts_when_nodes_exist(self):
        """Nodes with very low decay_score should be pruned immediately."""
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        try:
            # Insert an old node with very low decay score
            db.execute(
                "INSERT INTO nodes (id, type, name, user_id, decay_score, "
                "created_at, updated_at) "
                "VALUES ('n1', 'fact', 'old_fact', 'default', 0.03, "
                "'2020-01-01', '2020-01-01')"
            )
            db.commit()
            counts = run_decay(db, wq)
            assert counts["pruned"] >= 1  # The 0.03 node should be pruned
        finally:
            wq.stop()
            db.close()

    def test_returns_zero_counts_when_empty(self):
        """Empty DB should return all-zero counts."""
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        try:
            counts = run_decay(db, wq)
            assert counts["decayed"] == 0
            assert counts["speculated"] == 0
            assert counts["pruned"] == 0
            assert counts["archived"] == 0
        finally:
            wq.stop()
            db.close()

    def test_returns_dict_with_all_keys(self):
        """run_decay should always return all 4 count keys."""
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        try:
            counts = run_decay(db, wq)
            assert "decayed" in counts
            assert "speculated" in counts
            assert "pruned" in counts
            assert "archived" in counts
        finally:
            wq.stop()
            db.close()
