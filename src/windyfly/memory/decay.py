"""Cognitive decay — gradual forgetting of stale knowledge.

Runs periodically to decay old nodes, archive old episodes,
and prune ancient data. Decay rate is controlled by the
memory_retention slider (0=goldfish, 10=elephant).
"""

from __future__ import annotations

import logging
from typing import Any

from windyfly.control_panel import get_sliders
from windyfly.memory.database import Database
from windyfly.memory.write_queue import Priority, WriteQueue

logger = logging.getLogger(__name__)

# Mapping: memory_retention slider → (decay_multiplier, age_threshold_days)
# Slider 0 (goldfish): 0.90 multiplier, start decay after 7 days
# Slider 10 (elephant): 0.999 multiplier, start decay after 365 days
_RETENTION_MAP: dict[int, tuple[float, int]] = {
    0:  (0.90,   7),
    1:  (0.92,  14),
    2:  (0.94,  21),
    3:  (0.95,  30),
    4:  (0.96,  45),
    5:  (0.98,  60),   # default
    6:  (0.985, 90),
    7:  (0.99, 120),
    8:  (0.993, 180),
    9:  (0.996, 270),
    10: (0.999, 365),
}


def run_decay(
    db: Database,
    write_queue: WriteQueue,
    config: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Run the cognitive decay cycle.

    The memory_retention slider controls how aggressively old knowledge
    is forgotten:
    - 0 (goldfish): fast decay, start after 7 days
    - 10 (elephant): near-zero decay, start after 365 days

    Steps:
      1. Nodes: decay_score *= multiplier for nodes older than threshold
      2. Low-decay nodes (< 0.2): mark epistemic_status = 'speculative'
      3. Very low nodes (< 0.05): DELETE permanently
      4. Episodes: archive old episodes (> threshold * 3 days)

    Args:
        db: Database instance.
        write_queue: WriteQueue for async writes.
        config: Optional config dict for slider defaults.

    Returns:
        Dict with counts: decayed, speculated, pruned, archived.
    """
    # Read memory_retention slider
    config_defaults = (config or {}).get("personality", {})
    sliders = get_sliders(db, config_defaults=config_defaults)
    retention = sliders.get("memory_retention", 5)

    # Clamp to valid range and look up decay parameters
    retention = max(0, min(10, retention))
    decay_multiplier, age_threshold = _RETENTION_MAP.get(retention, (0.98, 60))

    # Archive threshold = 3x the decay age threshold
    archive_days = age_threshold * 3

    counts = {"decayed": 0, "speculated": 0, "pruned": 0, "archived": 0}

    def _do_decay():
        nonlocal counts

        # 1. Decay old nodes
        cursor = db.execute(
            f"""
            UPDATE nodes SET decay_score = decay_score * {decay_multiplier}
            WHERE updated_at < datetime('now', '-{age_threshold} days')
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
            f"""
            UPDATE episodes SET
                content = '[archived — original content pruned]',
                summary = COALESCE(summary, content)
            WHERE created_at < datetime('now', '-{archive_days} days')
              AND content != '[archived — original content pruned]'
            """,
        )
        counts["archived"] = cursor.rowcount

        db.commit()

        # Log event for observability (G12)
        from windyfly.observability.events import log_event
        log_event(db, write_queue, "decay.run", {
            "retention_slider": retention,
            "decay_multiplier": decay_multiplier,
            "age_threshold_days": age_threshold,
            **counts,
        })

        logger.info(
            "Decay cycle (retention=%d, mult=%.3f, age=%dd): "
            "%d decayed, %d speculated, %d pruned, %d archived",
            retention, decay_multiplier, age_threshold,
            counts["decayed"], counts["speculated"],
            counts["pruned"], counts["archived"],
        )

    # Execute synchronously when user-triggered (via API/dashboard)
    # so the returned counts reflect actual work done
    _do_decay()
    return counts
