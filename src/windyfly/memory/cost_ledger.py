"""Cost ledger CRUD operations.

Tracks API spend per LLM call: model, token counts, USD cost.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from windyfly.memory.database import Database


def log_cost(
    db: Database,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    *,
    task_type: str = "chat",
    request_id: str | None = None,
) -> str:
    """Log an API call cost to the ledger.

    Args:
        db: Database instance.
        model: Model name (e.g., 'gpt-4o-mini').
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.
        cost_usd: Cost in USD.
        task_type: Type of task (default: 'chat').
        request_id: Optional Wave 14 tracing correlation id.

    Returns:
        The generated ledger entry ID.
    """
    if request_id is None:
        from windyfly.agent.tracing import get_request_id
        request_id = get_request_id()
    entry_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO cost_ledger (id, model, input_tokens, output_tokens,
                                 cost_usd, task_type, request_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (entry_id, model, input_tokens, output_tokens, cost_usd,
         task_type, request_id),
    )
    db.commit()
    return entry_id


def get_daily_spend(db: Database) -> float:
    """Get total USD spent today.

    Returns:
        Total cost in USD for the current day.
    """
    row = db.fetchone(
        """
        SELECT COALESCE(SUM(cost_usd), 0.0) as total
        FROM cost_ledger
        WHERE created_at >= date('now', 'start of day')
        """
    )
    return row["total"] if row else 0.0


def get_monthly_spend(db: Database) -> float:
    """Get total USD spent this month.

    Returns:
        Total cost in USD for the current month.
    """
    row = db.fetchone(
        """
        SELECT COALESCE(SUM(cost_usd), 0.0) as total
        FROM cost_ledger
        WHERE created_at >= date('now', 'start of month')
        """
    )
    return row["total"] if row else 0.0


def get_recent_costs(
    db: Database,
    limit: int = 20,
) -> list[dict]:
    """Get most recent cost ledger entries.

    Args:
        db: Database instance.
        limit: Max entries to return.

    Returns:
        List of cost entry dicts.
    """
    return db.fetchall(
        "SELECT * FROM cost_ledger ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
