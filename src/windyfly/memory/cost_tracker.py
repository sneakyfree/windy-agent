"""Cost Tracker with budget enforcement.

Extends the base cost_ledger with monthly spend queries and
budget checking against config thresholds.
"""

from __future__ import annotations

from typing import Any

from windyfly.memory.cost_ledger import get_daily_spend, log_cost
from windyfly.memory.database import Database
from windyfly.memory.write_queue import Priority, WriteQueue


def get_monthly_spend(db: Database) -> float:
    """Get total spend for the current month.

    Returns:
        Total USD spend this month.
    """
    row = db.fetchone(
        """
        SELECT COALESCE(SUM(cost_usd), 0.0) as total
        FROM cost_ledger
        WHERE created_at >= date('now', 'start of month')
        """,
    )
    return float(row["total"]) if row else 0.0


def check_budget(
    db: Database,
    config: dict[str, Any],
    proposed_cost: float = 0.0,
) -> dict[str, Any]:
    """Check if a proposed cost is within budget.

    Args:
        db: Database instance.
        config: Config dict with costs.daily_budget_usd and costs.warn_at_usd.
        proposed_cost: The estimated cost of the next operation.

    Returns:
        Dict with: allowed, daily_spend, daily_budget, warning.
    """
    costs_config = config.get("costs", {})
    daily_budget = costs_config.get("daily_budget_usd", 5.0)
    warn_at = costs_config.get("warn_at_usd", 3.0)

    daily_spend = get_daily_spend(db) + proposed_cost

    return {
        "allowed": daily_spend <= daily_budget,
        "daily_spend": round(daily_spend, 4),
        "daily_budget": daily_budget,
        "warning": daily_spend > warn_at,
        "monthly_spend": round(get_monthly_spend(db), 4),
    }


def log_cost_async(
    db: Database,
    write_queue: WriteQueue,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    task_type: str = "chat",
) -> None:
    """Log cost via the write queue (MEDIUM priority)."""
    write_queue.enqueue(
        Priority.MEDIUM,
        log_cost,
        db, model, input_tokens, output_tokens, cost_usd,
    )
