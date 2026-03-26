"""Cognitive decay — gradual forgetting of stale knowledge.

Runs periodically to decay old nodes, archive old episodes,
and prune ancient data.
"""

from __future__ import annotations

import logging
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.write_queue import Priority, WriteQueue

logger = logging.getLogger(__name__)


def run_decay(db: Database, write_queue: WriteQueue) -> dict[str, int]:
    """Run the cognitive decay cycle.

    1. Nodes: decay_score *= 0.98 for nodes untouched > 30 days
    2. Low-decay nodes (< 0.2): mark epistemic_status = 'speculative'
    3. Very low nodes (< 0.05): DELETE permanently
    4. Episodes: archive old episodes (> 90 days)

    Args:
        db: Database instance.
        write_queue: WriteQueue for async writes.

    Returns:
        Dict with counts: decayed, speculated, pruned, archived.
    """
    counts = {"decayed": 0, "speculated": 0, "pruned": 0, "archived": 0}

    def _do_decay():
        nonlocal counts

        # 1. Decay old nodes
        cursor = db.execute(
            """
            UPDATE nodes SET decay_score = decay_score * 0.98
            WHERE updated_at < datetime('now', '-30 days')
              AND decay_score > 0.05
            """,
        )
        counts["decayed"] = cursor.rowcount

        # 2. Downgrade low-decay nodes to speculative
        cursor = db.execute(
            """
            UPDATE nodes SET epistemic_status = 'speculative'
            WHERE decay_score < 0.2
              AND decay_score >= 0.05
              AND epistemic_status != 'speculative'
            """,
        )
        counts["speculated"] = cursor.rowcount

        # 3. Prune very low nodes
        cursor = db.execute(
            """
            DELETE FROM nodes WHERE decay_score < 0.05
            """,
        )
        counts["pruned"] = cursor.rowcount

        # 4. Archive old episodes (replace content with summary placeholder)
        cursor = db.execute(
            """
            UPDATE episodes SET
                content = '[archived — original content pruned]',
                summary = COALESCE(summary, content)
            WHERE created_at < datetime('now', '-90 days')
              AND content != '[archived — original content pruned]'
            """,
        )
        counts["archived"] = cursor.rowcount

        db.commit()

        logger.info(
            "Decay cycle: %d decayed, %d speculated, %d pruned, %d archived",
            counts["decayed"], counts["speculated"],
            counts["pruned"], counts["archived"],
        )

    write_queue.enqueue(Priority.LOW, _do_decay)
    return counts
