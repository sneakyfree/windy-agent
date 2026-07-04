"""Skill-library curator — the gardener (Sprint 3).

The Hermes retention lesson: auto-created skills accumulate junk, and
without pruning the library rots until retrieval quality collapses.
Their Curator runs weekly; ours rides the existing 24h decay/drift
scheduler (main.py) — one more pass in a loop that already exists.

Conservative by design: DEMOTE-only, never delete. A demoted skill
drops out of the prompt index, skill.list, and file sync's promoted
set, but its row (and lineage) stays for rollback and forensics.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Promoted playbooks beyond this cap get demoted, least-recently-used
# first. Matches the prompt index (12) with generous headroom.
MAX_PROMOTED_PLAYBOOKS = 40
# A skill this unsuccessful stops being advice worth surfacing.
MIN_SUCCESS_RATIO = 0.34
MIN_USES_BEFORE_JUDGING = 4


def run_curation(db: Any) -> dict[str, int]:
    """One curation pass. Returns counters for the scheduler log."""
    from windyfly.skills.manager import demote_skill

    stats = {"demoted_failing": 0, "demoted_over_cap": 0, "kept": 0}
    rows = db.fetchall(
        "SELECT id, name, language, usage_count, success_count, "
        "failure_count, last_used FROM skills WHERE promoted = TRUE "
        "ORDER BY last_used DESC"
    )

    playbooks_kept = 0
    for row in rows:
        uses = row.get("usage_count") or 0
        successes = row.get("success_count") or 0
        failures = row.get("failure_count") or 0
        judged = successes + failures

        # Rule 1: consistently failing skills stop being advice.
        if judged >= MIN_USES_BEFORE_JUDGING and (
            successes / judged < MIN_SUCCESS_RATIO
        ):
            demote_skill(db, row["id"])
            stats["demoted_failing"] += 1
            logger.info(
                "curator: demoted failing skill %s (%d/%d successes)",
                row["name"], successes, judged,
            )
            continue

        # Rule 2: cap the promoted playbook library, LRU eviction.
        if row.get("language") == "playbook":
            playbooks_kept += 1
            if playbooks_kept > MAX_PROMOTED_PLAYBOOKS:
                demote_skill(db, row["id"])
                stats["demoted_over_cap"] += 1
                logger.info(
                    "curator: demoted over-cap playbook %s (uses=%d)",
                    row["name"], uses,
                )
                continue

        stats["kept"] += 1

    if stats["demoted_failing"] or stats["demoted_over_cap"]:
        logger.info("curator pass: %s", stats)
    return stats
