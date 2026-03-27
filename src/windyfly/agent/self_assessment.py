"""Self-assessment — the agent's weekly report card.

Grades the agent on 6 metrics using data already in the database.
Stores results as type=self_assessment nodes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.nodes import upsert_node

logger = logging.getLogger(__name__)


def run_self_assessment(db: Database) -> dict[str, Any]:
    """Run a self-assessment and return the report card.

    Metrics (each scored 0-100):
      1. Memory Retention: nodes created vs decayed this week
      2. Failure Rate: failures logged vs resolved this week
      3. Soul Currency: hours since last soul/personality update
      4. Relationship Depth: relationship moments accumulated
      5. Response Consistency: average response cost this week
      6. Cost Efficiency: tokens per interaction this week

    Returns:
        Dict with scores and overall grade.
    """
    scores: dict[str, float] = {}

    # 1. Memory Retention
    total_nodes = db.fetchone("SELECT COUNT(*) as c FROM nodes")
    recent_nodes = db.fetchone(
        "SELECT COUNT(*) as c FROM nodes WHERE created_at >= date('now', '-7 days')"
    )
    total = total_nodes["c"] if total_nodes else 0
    recent = recent_nodes["c"] if recent_nodes else 0
    scores["memory_retention"] = min(100, (recent / max(total, 1)) * 500)

    # 2. Failure Rate
    total_failures = db.fetchone(
        "SELECT COUNT(*) as c FROM failures WHERE created_at >= date('now', '-7 days')"
    )
    resolved_failures = db.fetchone(
        "SELECT COUNT(*) as c FROM failures WHERE resolved_at IS NOT NULL AND created_at >= date('now', '-7 days')"
    )
    total_f = total_failures["c"] if total_failures else 0
    resolved_f = resolved_failures["c"] if resolved_failures else 0
    scores["failure_rate"] = (resolved_f / max(total_f, 1)) * 100 if total_f > 0 else 100

    # 3. Soul Currency (freshness)
    soul_update = db.fetchone(
        "SELECT MAX(updated_at) as last FROM soul"
    )
    if soul_update and soul_update["last"]:
        from datetime import datetime, timezone
        try:
            last = datetime.fromisoformat(soul_update["last"].replace("Z", "+00:00"))
            hours_ago = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            scores["soul_currency"] = max(0, 100 - (hours_ago * 2))  # Lose 2pts per hour
        except (ValueError, TypeError):
            scores["soul_currency"] = 50
    else:
        scores["soul_currency"] = 0

    # 4. Relationship Depth
    moments = db.fetchone(
        "SELECT COUNT(*) as c FROM nodes WHERE type = 'relationship_moment'"
    )
    moment_count = moments["c"] if moments else 0
    scores["relationship_depth"] = min(100, moment_count * 10)  # 10 moments = 100%

    # 5. Response Consistency
    avg_cost = db.fetchone(
        "SELECT AVG(cost_usd) as avg FROM cost_ledger WHERE created_at >= date('now', '-7 days')"
    )
    avg = avg_cost["avg"] if avg_cost and avg_cost["avg"] else 0
    # Low average cost = high consistency (efficient)
    scores["response_consistency"] = max(0, 100 - (avg * 10000))

    # 6. Cost Efficiency
    total_cost = db.fetchone(
        "SELECT SUM(cost_usd) as total FROM cost_ledger WHERE created_at >= date('now', '-7 days')"
    )
    total_interactions = db.fetchone(
        "SELECT COUNT(*) as c FROM episodes WHERE role = 'user' AND created_at >= date('now', '-7 days')"
    )
    tc = total_cost["total"] if total_cost and total_cost["total"] else 0
    ti = total_interactions["c"] if total_interactions else 0
    cost_per = tc / max(ti, 1)
    scores["cost_efficiency"] = max(0, 100 - (cost_per * 5000))

    # Overall grade (weighted average)
    overall = sum(scores.values()) / len(scores) if scores else 0
    grade = _score_to_grade(overall)

    report = {
        "scores": {k: round(v, 1) for k, v in scores.items()},
        "overall_score": round(overall, 1),
        "grade": grade,
    }

    # Store as node
    upsert_node(
        db,
        "self_assessment",
        f"assessment:{grade}:{round(overall, 1)}",
        metadata=report,
        source="self_assessment",
        epistemic_status="verified",
    )

    return report


def _score_to_grade(score: float) -> str:
    """Convert a 0-100 score to a letter grade."""
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    if score >= 50:
        return "D"
    return "F"
