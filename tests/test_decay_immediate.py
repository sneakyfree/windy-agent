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


class TestChronicleLaw1:
    """Chronicle Doctrine Law 1 (2026-07-18): decay may DIM, never ERASE.

    Pins the fix for the live landmine found during the doctrine
    review: the old step 3 hard-DELETEd nodes < 0.05 and the old step 4
    overwrote episode content with '[archived — original content
    pruned]' past ~archive_days. Neither may ever return.
    """

    def _db(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        return db, wq

    def test_low_score_nodes_survive_with_floored_weight(self):
        db, wq = self._db()
        try:
            db.execute(
                "INSERT INTO nodes (id, type, name, user_id, decay_score, "
                "created_at, updated_at) "
                "VALUES ('n1', 'fact', 'ancient_fact', 'default', 0.03, "
                "'2020-01-01', '2020-01-01')"
            )
            db.commit()
            counts = run_decay(db, wq)
            assert counts["pruned"] >= 1  # counted, but…
            row = db.fetchone("SELECT decay_score FROM nodes WHERE id='n1'")
            assert row is not None, "Law 1 violated: node was hard-deleted"
            assert row["decay_score"] == 0.01
        finally:
            wq.stop()
            db.close()

    def test_ancient_episode_content_never_touched(self):
        db, wq = self._db()
        try:
            db.execute(
                "INSERT INTO episodes (id, role, content, session_id, "
                "created_at) VALUES ('e1', 'user', "
                "'my dog is named Biscuit', 's:1:v1', '2020-01-01')"
            )
            db.commit()
            counts = run_decay(db, wq)
            assert counts["archived"] == 0
            row = db.fetchone("SELECT content FROM episodes WHERE id='e1'")
            assert row["content"] == "my dog is named Biscuit", (
                "Law 1 violated: raw episode content was rewritten"
            )
        finally:
            wq.stop()
            db.close()

    def test_goldfish_slider_still_never_erases(self):
        """Even at retention 0 (goldfish), the raw record survives."""
        db, wq = self._db()
        try:
            from windyfly.control_panel import set_slider
            set_slider(db, "memory_retention", 0)
            db.execute(
                "INSERT INTO episodes (id, role, content, session_id, "
                "created_at) VALUES ('e2', 'assistant', 'the reply', "
                "'s:1:v1', '2019-06-01')"
            )
            db.execute(
                "INSERT INTO nodes (id, type, name, user_id, decay_score, "
                "created_at, updated_at) VALUES ('n2', 'fact', 'f', "
                "'default', 0.02, '2019-06-01', '2019-06-01')"
            )
            db.commit()
            run_decay(db, wq)
            assert db.fetchone("SELECT 1 FROM nodes WHERE id='n2'") is not None
            row = db.fetchone("SELECT content FROM episodes WHERE id='e2'")
            assert row["content"] == "the reply"
        finally:
            wq.stop()
            db.close()
