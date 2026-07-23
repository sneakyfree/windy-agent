"""Cognitive decay — retrieval emphasis, NOT forgetting.

Runs periodically to lower the retrieval weight (decay_score) of stale
nodes and downgrade their epistemic status. Rate is controlled by the
memory_retention slider (0=goldfish, 10=elephant) — but per the
Chronicle Doctrine (Law 1, 2026-07-18), decay may only DIM, never
ERASE:

  - Nodes are never hard-deleted; decay_score floors at 0.01 and very
    stale nodes become 'speculative'. The words survive.
  - Raw episode content is NEVER touched. The previous step-4 here
    overwrote episode content with '[archived — original content
    pruned]' after ~archive_days — a scheduled destruction of the
    Chronicle that had (verified 2026-07-18) not yet fired on any live
    row. It is gone; do not reintroduce it. The Chronicle is
    append-only: no machine decides a memory is unworthy of keeping.
"""

from __future__ import annotations

import logging
from typing import Any

from windyfly.control_panel import get_sliders
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

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
    is DE-EMPHASIZED in retrieval (never destroyed):
    - 0 (goldfish): fast de-emphasis, start after 7 days
    - 10 (elephant): near-zero de-emphasis, start after 365 days

    Steps:
      1. Nodes: decay_score *= multiplier for nodes older than threshold
      2. Low-decay nodes (< 0.2): mark epistemic_status = 'speculative'
      3. Very low nodes (< 0.05): FLOOR decay_score at 0.01 (counted as
         'pruned' for backward-compatible reporting — pruned from
         retrieval emphasis, not from existence)
      4. Episodes: NEVER touched (Chronicle Doctrine Law 1). The
         'archived' count is always 0 and retained only for
         report-shape compatibility.

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

        # 3. Floor very low nodes at minimum retrieval weight. The old
        # behavior here was DELETE — a machine deciding a fact was
        # unworthy of keeping, forbidden under Chronicle Doctrine Law 1.
        # The node stays (speculative, near-zero weight); the record
        # survives for a smarter future model to re-evaluate.
        cursor = db.execute(
            """
            UPDATE nodes SET decay_score = 0.01
            WHERE decay_score < 0.05 AND decay_score != 0.01
            """,
        )
        counts["pruned"] = cursor.rowcount

        # 4. Episodes: deliberately untouched. The Chronicle is
        # append-only raw; decay has no business here. ('archived' stays
        # 0 in reports for continuity of dashboards.)
        counts["archived"] = 0

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
