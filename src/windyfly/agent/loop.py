"""Core agent loop — the ReAct reasoning cycle.

Handles the full cycle: prompt assembly → LLM call → episode save →
cost logging → fact extraction.
"""

from __future__ import annotations

import logging
from typing import Any

from windyfly.agent.models import call_llm, estimate_cost
from windyfly.agent.prompt import assemble_prompt
from windyfly.memory.cost_ledger import log_cost
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.memory.nodes import upsert_node
from windyfly.memory.write_queue import Priority, WriteQueue

logger = logging.getLogger(__name__)


def agent_respond(
    config: dict[str, Any],
    db: Database,
    write_queue: WriteQueue,
    user_message: str,
    session_id: str,
) -> str:
    """Process a user message and return the agent's response.

    1. Assemble prompt (personality + memory + context + user message)
    2. Call LLM
    3. Save episodes (user + assistant) via write queue
    4. Log cost via write queue
    5. Extract facts and upsert nodes via write queue
    6. Return the agent's response text

    Args:
        config: Loaded config dict.
        db: Database instance.
        write_queue: WriteQueue for async DB writes.
        user_message: The user's message.
        session_id: Current session ID.

    Returns:
        The agent's response text.
    """
    # 1. Assemble prompt
    messages = assemble_prompt(config, db, user_message, session_id)

    # 2. Call LLM
    model = config.get("agent", {}).get("default_model", "gpt-4o-mini")
    temperature = config.get("agent", {}).get("temperature", 0.7)
    max_tokens = config.get("agent", {}).get("max_response_tokens", 2000)

    result = call_llm(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        config=config,
    )

    response_text = result["content"]
    input_tokens = result["input_tokens"]
    output_tokens = result["output_tokens"]

    # 3. Save episodes via write queue (HIGH priority)
    cost_usd = estimate_cost(model, input_tokens, output_tokens)

    write_queue.enqueue(
        Priority.HIGH,
        save_episode,
        db, "user", user_message,
        session_id=session_id,
    )
    write_queue.enqueue(
        Priority.HIGH,
        save_episode,
        db, "assistant", response_text,
        session_id=session_id,
        token_count=output_tokens,
        cost_usd=cost_usd,
    )

    # 4. Log cost via write queue (MEDIUM priority)
    write_queue.enqueue(
        Priority.MEDIUM,
        log_cost,
        db, model, input_tokens, output_tokens, cost_usd,
    )

    # 5. Extract facts and upsert nodes (MEDIUM priority)
    _extract_and_store_facts(db, write_queue, user_message)

    return response_text


def _extract_and_store_facts(
    db: Database,
    write_queue: WriteQueue,
    user_message: str,
) -> None:
    """Extract obvious facts from the user message and store as nodes.

    Simple pattern-based extraction for Phase 0. More sophisticated
    LLM-based extraction will come in later phases.

    Patterns detected:
    - "My name is X"
    - "I am X" / "I'm X"
    - "I live in X"
    - "I like X" / "I love X"
    - "I work at X" / "I work as X"
    """
    import re

    patterns = [
        (r"(?i)my name is (.+?)(?:\.|,|!|\?|$)", "person", "user_name", "user_stated"),
        (r"(?i)i(?:'m| am) (.+?)(?:\.|,|!|\?|$)", "trait", "user_trait", "user_stated"),
        (r"(?i)i live in (.+?)(?:\.|,|!|\?|$)", "location", "user_location", "user_stated"),
        (r"(?i)i (?:like|love) (.+?)(?:\.|,|!|\?|$)", "preference", "user_preference", "user_stated"),
        (r"(?i)i work (?:at|as|for) (.+?)(?:\.|,|!|\?|$)", "work", "user_work", "user_stated"),
    ]

    for pattern, node_type, name_prefix, source in patterns:
        match = re.search(pattern, user_message)
        if match:
            value = match.group(1).strip()
            if len(value) > 2 and len(value) < 100:
                write_queue.enqueue(
                    Priority.MEDIUM,
                    upsert_node,
                    db,
                    node_type,
                    f"{name_prefix}:{value}",
                    metadata={"raw_statement": user_message[:200]},
                    source=source,
                    epistemic_status="user_stated",
                )
