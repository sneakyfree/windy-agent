"""Cost ledger CRUD operations.

Tracks API spend per LLM call: model, token counts, USD cost.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from windyfly.memory.write_queue import Priority

if TYPE_CHECKING:
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue


def log_cost(
    db: Database,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None,
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
        cost_usd: Cost in USD, or None when it couldn't be priced
            (stored as NULL: "unknown", never $0).
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


def log_llm_call(db: Database, record: dict[str, Any]) -> str:
    """Write one per-call record (see ``models.call_llm``) to the ledger."""
    entry_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO cost_ledger (id, model, input_tokens, output_tokens,
                                 cost_usd, task_type, request_id, provider,
                                 status, error_code, billing,
                                 cache_write_tokens, cache_read_tokens,
                                 session_id, duration_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_id,
            record.get("model") or "unknown",
            int(record.get("input_tokens") or 0),
            int(record.get("output_tokens") or 0),
            record.get("cost_usd"),
            record.get("purpose") or "chat",
            record.get("request_id"),
            record.get("provider"),
            record.get("status"),
            record.get("error_code"),
            record.get("billing"),
            int(record.get("cache_write_tokens") or 0),
            int(record.get("cache_read_tokens") or 0),
            record.get("session_id"),
            record.get("duration_ms"),
        ),
    )
    db.commit()
    return entry_id


def install_cost_sink(db: Database, write_queue: WriteQueue) -> None:
    """Route every LLM call in this process to this ledger (idempotent).

    Rides the write queue (MEDIUM) like the rest of the ledger, and
    mirrors each record to Windy Admin as an ``llm.call`` row (a no-op
    unless configured).
    """
    from windyfly.agent import models

    owner = (id(db), id(write_queue))
    if models.cost_sink_owner() == owner:
        return

    def sink(record: dict[str, Any]) -> None:
        write_queue.enqueue(Priority.MEDIUM, log_llm_call, db, dict(record))
        try:
            from windyfly.observability import agent_health
            from windyfly.observability.admin_telemetry import emit_llm_record
            agent_health.note_llm_record(record)
            emit_llm_record(write_queue, record)
        except Exception:
            pass  # telemetry never breaks accounting

    models.set_cost_sink(sink, owner)
    try:
        # Once per process: service.boot + the 15-min service.health timer.
        from windyfly.observability import agent_health
        agent_health.start(write_queue)
    except Exception:
        pass  # telemetry never blocks a boot


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
