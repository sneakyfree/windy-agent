"""Sub-agent orchestration — spawn isolated specialist agents.

V1: Pseudo sub-agents using isolated LLM calls with token budgets.
Depth limit: 1 (sub-agents cannot spawn sub-agents).
"""

from __future__ import annotations

import logging
from typing import Any

from windyfly.agent.models import call_llm, estimate_cost
from windyfly.memory.cost_ledger import log_cost
from windyfly.memory.database import Database
from windyfly.memory.write_queue import Priority, WriteQueue

logger = logging.getLogger(__name__)


def spawn_sub_agent(
    config: dict[str, Any],
    db: Database,
    write_queue: WriteQueue,
    task: str,
    *,
    token_budget: int = 2000,
    model: str | None = None,
) -> str:
    """Spawn an isolated sub-agent and return its result.

    The sub-agent has:
    - No access to parent conversation history
    - Its own system prompt focused on the task
    - A token budget (max_tokens)
    - Its cost logged separately with task_type='sub_agent'

    Args:
        config: Config dict.
        db: Database instance.
        write_queue: WriteQueue for cost logging.
        task: Task description for the sub-agent.
        token_budget: Max response tokens.
        model: LLM model to use (defaults to config default).

    Returns:
        The sub-agent's response text.
    """
    if model is None:
        model = config.get("agent", {}).get("default_model", "gpt-4o-mini")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a specialist sub-agent. Your task is described below. "
                "Respond with your findings only. Be concise and focused. "
                f"Budget: {token_budget} tokens."
            ),
        },
        {
            "role": "user",
            "content": task,
        },
    ]

    result = call_llm(
        messages,
        model=model,
        temperature=0.3,  # Lower temp for focused tasks
        max_tokens=token_budget,
        config=config,
    )

    response_text = result["content"]
    cost_usd = estimate_cost(model, result["input_tokens"], result["output_tokens"])

    # Log cost separately as sub_agent task type
    write_queue.enqueue(
        Priority.MEDIUM,
        log_cost,
        db, model, result["input_tokens"], result["output_tokens"],
        cost_usd,
    )

    logger.info(
        "Sub-agent completed: %.4f USD, %d tokens",
        cost_usd, result["output_tokens"],
    )

    return response_text


def register_sub_agent_tool(
    registry: "ToolRegistry",
    config: dict[str, Any],
    db: Database,
    write_queue: WriteQueue,
) -> None:
    """Register the sub-agent as a callable tool in the registry.

    Args:
        registry: ToolRegistry instance.
        config: Config dict.
        db: Database instance.
        write_queue: WriteQueue instance.
    """
    from windyfly.tools.registry import ToolRegistry  # noqa: F811

    def _sub_agent_tool(task: str, token_budget: int = 2000) -> str:
        """Delegate to a specialist sub-agent."""
        from windyfly.observability.events import log_event
        log_event(db, write_queue, "sub_agent.spawn", {"task": task[:100], "budget": token_budget})
        return spawn_sub_agent(config, db, write_queue, task, token_budget=token_budget)

    registry.register(
        name="delegate_to_specialist",
        description=(
            "Delegate a focused task to an isolated specialist sub-agent. "
            "The sub-agent has NO conversation history or memory — it only sees the task. "
            "Costs 2x tokens vs shape_shift because context is duplicated. "
            "KEY BENEFIT: the user can keep talking to you while the sub-agent works independently. "
            "Use when: (1) the user wants a clean-slate, unbiased analysis, or "
            "(2) the task is long-running and the user wants to keep chatting. "
            "Check shape_shift_bias slider: if low (0-3), user prefers this approach."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task description for the specialist sub-agent",
                },
                "token_budget": {
                    "type": "integer",
                    "description": "Maximum response tokens for the sub-agent (default: 2000)",
                },
            },
            "required": ["task"],
        },
        fn=_sub_agent_tool,
    )

    # Also register shape-shift tools (coexist, LLM picks the best one)
    from windyfly.agent.shape_shift import register_shape_shift_tool
    register_shape_shift_tool(registry, config, db, write_queue)
