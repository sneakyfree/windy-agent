"""Cost Tracker with budget enforcement and alerts.

Extends the base cost_ledger with monthly spend queries,
budget checking, percentage-based warnings, and alert generation.
"""

from __future__ import annotations

import logging
from typing import Any

from windyfly.memory.cost_ledger import get_daily_spend, log_cost
from windyfly.memory.database import Database
from windyfly.memory.write_queue import Priority, WriteQueue

logger = logging.getLogger(__name__)


def get_monthly_spend(db: Database) -> float:
    """Get total spend for the current month."""
    row = db.fetchone(
        """
        SELECT COALESCE(SUM(cost_usd), 0.0) as total
        FROM cost_ledger
        WHERE created_at >= date('now', 'start of month')
        """,
    )
    return float(row["total"]) if row else 0.0


# "Continue today" (strand P3.32.6): the owner may let the agent go on past the daily budget for the rest of
# the UTC day, by one more day's budget, once. It resets by itself at midnight UTC (the same day boundary
# the daily spend uses). The monthly budget is never overridden.
CONTINUE_KEY = "budget_continue"


def _today(db: Database) -> str:
    row = db.fetchone("SELECT date('now') AS d")
    return str((row or {}).get("d") or "")


def extra_today(db: Database) -> float:
    """The extra allowance the owner granted for today, or 0."""
    import json

    from windyfly.memory.soul import get_soul

    row = get_soul(db, CONTINUE_KEY)
    try:
        data = json.loads(row["value"]) if row and row.get("value") else {}
    except (ValueError, TypeError):
        return 0.0
    if not isinstance(data, dict) or data.get("date") != _today(db):
        return 0.0
    try:
        return max(0.0, float(data.get("extra", 0)))
    except (TypeError, ValueError):
        return 0.0


def continue_today(db: Database, config: dict[str, Any]) -> dict[str, Any]:
    """Grant one more day's budget for the rest of today (once). Returns what happened."""
    import json

    from windyfly.memory.soul import upsert_soul

    if extra_today(db) > 0:
        return {"granted": False, "already": True}
    extra = float((config.get("costs") or {}).get("daily_budget_usd", 5.0))
    upsert_soul(db, CONTINUE_KEY, json.dumps({"date": _today(db), "extra": extra}), source="control_panel")
    try:
        from windyfly.observability.events import record_event

        record_event(db, "budget.continue", {"extra_usd": round(extra, 2)})
    except Exception:  # noqa: BLE001
        pass
    return {"granted": True, "extra": extra}


def check_budget(
    db: Database,
    config: dict[str, Any],
    proposed_cost: float = 0.0,
) -> dict[str, Any]:
    """Check if a proposed cost is within budget.

    Enhanced with:
    - Monthly budget enforcement
    - Percentage-based warnings (warn_at_percent, default 80%)
    - Alert messages for the agent to relay to the user
    """
    costs_config = config.get("costs", {})
    daily_budget = costs_config.get("daily_budget_usd", 5.0)
    monthly_budget = costs_config.get("monthly_budget_usd", 0.0)  # 0 = no monthly limit
    warn_pct = costs_config.get("warn_at_percent", 80)

    daily_spend = get_daily_spend(db) + proposed_cost
    monthly_spend = get_monthly_spend(db) + proposed_cost
    extra = extra_today(db)

    # Daily budget check (plus the owner's "continue today", if granted today)
    daily_allowed = daily_spend <= daily_budget + extra
    daily_pct = (daily_spend / daily_budget * 100) if daily_budget > 0 else 0

    # Monthly budget check
    monthly_allowed = True
    monthly_pct = 0.0
    if monthly_budget > 0:
        monthly_allowed = monthly_spend <= monthly_budget
        monthly_pct = (monthly_spend / monthly_budget * 100) if monthly_budget > 0 else 0

    allowed = daily_allowed and monthly_allowed

    # Generate alert message if needed
    alert = None
    if not daily_allowed and extra > 0:
        alert = (
            f"I've used today's budget and the extra you allowed (${daily_spend:.2f} / "
            f"${daily_budget + extra:.2f}). I'll be back tomorrow, or you can increase the budget in settings."
        )
    elif not daily_allowed:
        alert = (
            f"I've hit my daily budget (${daily_spend:.2f} / ${daily_budget:.2f}). "
            f"I'll be back tomorrow. To keep going today, my owner can type /budget continue "
            f"(up to another ${daily_budget:.2f} today), or increase the budget in settings."
        )
    elif not monthly_allowed:
        alert = (
            f"I've hit my monthly budget (${monthly_spend:.2f} / ${monthly_budget:.2f}). "
            "You can increase it in windyfly.toml under [costs] monthly_budget_usd."
        )
    elif daily_pct >= warn_pct:
        alert = (
            f"Heads up: I've used ${daily_spend:.2f} of your ${daily_budget:.2f} daily budget "
            f"({daily_pct:.0f}%)."
        )
    elif monthly_budget > 0 and monthly_pct >= warn_pct:
        alert = (
            f"Monthly budget heads up: ${monthly_spend:.2f} of ${monthly_budget:.2f} "
            f"({monthly_pct:.0f}%)."
        )

    return {
        "allowed": allowed,
        "daily_spend": round(daily_spend, 4),
        "daily_budget": daily_budget,
        "extra_today": round(extra, 4),
        "daily_percent": round(daily_pct, 1),
        "monthly_spend": round(monthly_spend, 4),
        "monthly_budget": monthly_budget,
        "monthly_percent": round(monthly_pct, 1),
        "warning": daily_pct >= warn_pct or (monthly_budget > 0 and monthly_pct >= warn_pct),
        "alert": alert,
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
