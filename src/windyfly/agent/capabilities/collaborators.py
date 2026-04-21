"""Collaborator capabilities — Wave 6 #1.

Replaces the legacy depth-1 sub_agent (one-shot, no memory) with
long-running named entities that persist across sessions and
optionally see filtered slices of the parent's memory.

Why this beats Hermes' delegate_task:

  - Hermes: depth-2, no memory inheritance, every subagent cold-starts
  - Wave 6 #1: persist-forever (Decision W6-1), topic-filter memory
    share (W6-2), recursion=1 cap (W6-5), parent_action_id audit
    linking (W6-7)

The "research" collaborator that's been around for 3 weeks knows the
user's research preferences (depth, formatting, source trust) better
than one spawned fresh per task. That continuity is the moat.

Capabilities exposed to the LLM:

  agent.list_collaborators       Tier.READ_EXTERNAL  USER+
  agent.create_collaborator      Tier.EXTERNAL_EFFECT TRUSTED+
  agent.archive_collaborator     Tier.WRITE_DESTRUCTIVE TRUSTED+
  agent.delegate_to              Tier.EXTERNAL_EFFECT TRUSTED+
"""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from typing import Any

from windyfly.agent.capabilities.descriptor import (
    Band,
    Capability,
    CapabilityDenied,
    Tier,
)
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.agent.models import call_llm
from windyfly.memory.collaborators import (
    DEFAULT_MEMORY_POLICY,
    archive_collaborator,
    create_collaborator,
    get_collaborator_by_name,
    list_collaborators,
    parse_memory_policy,
    record_use,
)
from windyfly.memory.database import Database
from windyfly.memory.episodes import get_recent_episodes, save_episode
from windyfly.memory.write_queue import Priority, WriteQueue

logger = logging.getLogger(__name__)

# Recursion guard (Decision W6-5). When set, agent.delegate_to refuses
# to spawn another collaborator — depth=1 max. Implemented as a
# contextvar so concurrent collaborator runs from the parent don't
# leak the flag into each other.
_inside_collaborator: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "inside_collaborator_turn", default=False,
)

# Per-collaborator session id format. The session_id distinguishes
# collaborator turns from the parent's turns in the episodes table,
# which is how we get persistence-across-sessions: next time a
# collaborator with the same name runs, it loads its own past
# episodes via this prefix.
def _collaborator_session_id(collaborator_name: str, parent_session_id: str) -> str:
    return f"collab:{collaborator_name}:{parent_session_id}"


def _build_filtered_memory_summary(
    db: Database,
    collaborator: dict[str, Any],
) -> str:
    """Build a system-prompt slice describing what the collaborator
    is allowed to know about its parent. Topic-filter memory share
    per Decision W6-2.
    """
    policy = parse_memory_policy(collaborator["memory_share_policy"])
    parent_user_id = collaborator["parent_user_id"]
    lines: list[str] = []

    if policy.get("include_personality", True):
        # Pull the parent's persona slider summary (best-effort)
        soul_rows = db.fetchall(
            "SELECT key, value FROM soul WHERE user_id = ? ORDER BY key",
            (parent_user_id,),
        )
        if soul_rows:
            lines.append("Your parent agent's personality summary:")
            for row in soul_rows[:20]:  # cap to avoid prompt bloat
                lines.append(f"  - {row['key']}: {row['value']}")

    node_types = policy.get("node_types") or []
    keywords = policy.get("topic_keywords") or []
    if node_types or keywords:
        # Build a SQL filter for nodes the collaborator is allowed to see
        clauses: list[str] = ["user_id = ?"]
        params: list[Any] = [parent_user_id]
        if node_types:
            placeholders = ",".join("?" for _ in node_types)
            clauses.append(f"type IN ({placeholders})")
            params.extend(node_types)
        if keywords:
            kw_clauses = " OR ".join("name LIKE ?" for _ in keywords)
            clauses.append(f"({kw_clauses})")
            params.extend(f"%{kw}%" for kw in keywords)
        node_rows = db.fetchall(
            f"SELECT type, name FROM nodes WHERE {' AND '.join(clauses)} "
            "ORDER BY updated_at DESC LIMIT 30",
            tuple(params),
        )
        if node_rows:
            lines.append("\nRelevant facts from your parent's memory:")
            for row in node_rows:
                lines.append(f"  - [{row['type']}] {row['name']}")

    if policy.get("include_intents"):
        intent_rows = db.fetchall(
            "SELECT description FROM intents WHERE user_id = ? "
            "AND status = 'active' ORDER BY priority DESC LIMIT 10",
            (parent_user_id,),
        )
        if intent_rows:
            lines.append("\nYour parent's active goals:")
            for row in intent_rows:
                lines.append(f"  - {row['description']}")

    if not lines:
        lines.append(
            "(You have no shared memory with your parent agent — "
            "your parent didn't grant any topic access.)"
        )
    return "\n".join(lines)


def _run_collaborator_turn(
    db: Database,
    write_queue: WriteQueue,
    config: dict[str, Any],
    collaborator: dict[str, Any],
    task: str,
    parent_session_id: str,
) -> str:
    """Drive one collaborator turn: load past collaborator-internal
    episodes, build the prompt, call_llm, save episodes, return the
    response.

    Memory persistence: each collaborator's episodes use a session_id
    of ``collab:<name>:<parent_session>``. Subsequent invocations of
    the same collaborator load these episodes as context — that's
    the "persistent across sessions" promise. The parent's episodes
    table is shared across all of these.
    """
    session_id = _collaborator_session_id(
        collaborator["name"], parent_session_id,
    )

    # Load past collaborator turns (last 10 exchanges) for continuity
    past = get_recent_episodes(db, limit=20, session_id=session_id)
    past.reverse()  # chronological order

    # Build the system prompt: persona + filtered memory summary
    persona = collaborator["persona_prompt"]
    memory_summary = _build_filtered_memory_summary(db, collaborator)
    system_prompt = (
        f"You are {collaborator['name']}, a specialist collaborator.\n\n"
        f"{persona}\n\n"
        f"{memory_summary}\n\n"
        "You were spawned by your parent agent to handle a specific "
        "task. Return a single concise response. You cannot delegate "
        "to other collaborators (recursion is capped at 1)."
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]
    for ep in past:
        if ep["role"] in ("user", "assistant"):
            messages.append({"role": ep["role"], "content": ep["content"]})
    messages.append({"role": "user", "content": task})

    # Mark this thread as inside-a-collaborator so any nested
    # delegate_to call refuses (depth cap = 1).
    token = _inside_collaborator.set(True)
    try:
        max_tokens = collaborator.get("max_context_tokens", 8000) // 4
        model = collaborator.get("model")
        result = call_llm(
            messages,
            model=model,
            max_tokens=max_tokens,
            config=config,
        )
    finally:
        _inside_collaborator.reset(token)

    response_text = result.get("content", "") or ""

    # Persist this turn for the next call's continuity
    write_queue.enqueue(
        Priority.HIGH, save_episode, db, "user", task,
        session_id=session_id,
    )
    write_queue.enqueue(
        Priority.HIGH, save_episode, db, "assistant", response_text,
        session_id=session_id,
    )

    record_use(db, write_queue, collaborator_id=collaborator["id"])
    return response_text


def register_collaborator_capabilities(
    registry: CapabilityRegistry,
    db: Database,
    write_queue: WriteQueue,
    config: dict[str, Any] | None = None,
) -> None:
    """Register the four agent.* collaborator capabilities."""
    cfg = config or {}

    # ── agent.list_collaborators ────────────────────────────────────

    def list_handler(
        *, include_archived: bool = False,
    ) -> dict[str, Any]:
        rows = list_collaborators(db, include_archived=include_archived)
        return {
            "count": len(rows),
            "collaborators": [
                {
                    "name": r["name"],
                    "id": r["id"],
                    "persona_prompt": r["persona_prompt"][:200],
                    "band": r["band"],
                    "use_count": r["use_count"],
                    "last_used_at": r["last_used_at"],
                    "archived": r["archived_at"] is not None,
                }
                for r in rows
            ],
        }

    registry.register(Capability(
        id="agent.list_collaborators",
        description=(
            "List the long-running named collaborators available to "
            "this agent. Each collaborator persists across sessions "
            "and optionally shares filtered slices of your memory."
        ),
        handler=list_handler,
        input_schema={
            "type": "object",
            "properties": {
                "include_archived": {"type": "boolean"},
            },
            "required": [],
        },
        tier=Tier.READ_EXTERNAL,
        scope="collaborator_metadata",
    ))

    # ── agent.create_collaborator ──────────────────────────────────

    def create_handler(
        *, name: str, persona_prompt: str,
        memory_filter: dict[str, Any] | None = None,
        band: str = "USER",
        model: str | None = None,
        daily_budget_usd: float = 1.0,
    ) -> dict[str, Any]:
        try:
            collab_id = create_collaborator(
                db, write_queue,
                name=name,
                persona_prompt=persona_prompt,
                memory_share_policy=memory_filter or DEFAULT_MEMORY_POLICY,
                band=band,
                model=model,
                daily_budget_usd=daily_budget_usd,
            )
        except ValueError as e:
            return {"created": False, "error": str(e)}
        return {
            "created": True,
            "id": collab_id,
            "name": name,
            "memory_filter": memory_filter or DEFAULT_MEMORY_POLICY,
        }

    registry.register(Capability(
        id="agent.create_collaborator",
        description=(
            "Create a new long-running named collaborator with a "
            "persona and an optional memory_filter that controls "
            "what slices of your memory it can see. The collaborator "
            "persists across sessions; use agent.delegate_to to give "
            "it tasks, agent.archive_collaborator to retire it."
        ),
        handler=create_handler,
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Unique name (e.g., 'research', 'writing').",
                },
                "persona_prompt": {
                    "type": "string",
                    "description": (
                        "What kind of collaborator this is — used as the "
                        "system prompt's identity section."
                    ),
                },
                "memory_filter": {
                    "type": "object",
                    "description": (
                        "Topic-filter memory share. Keys: include_personality "
                        "(bool), node_types (list), topic_keywords (list), "
                        "include_intents (bool). Default: personality only."
                    ),
                },
                "band": {"type": "string"},
                "model": {"type": "string"},
                "daily_budget_usd": {"type": "number"},
            },
            "required": ["name", "persona_prompt"],
        },
        tier=Tier.EXTERNAL_EFFECT,
        scope="collaborator_lifecycle",
    ))

    # ── agent.archive_collaborator ─────────────────────────────────

    def archive_handler(*, name: str) -> dict[str, Any]:
        archived = archive_collaborator(db, write_queue, name=name)
        return {
            "archived": archived,
            "name": name,
            "note": (
                "Collaborator soft-deleted (rows preserved). Recreate "
                "with the same name to start fresh, or query with "
                "include_archived=true."
            ) if archived else (
                f"No active collaborator named {name!r} found."
            ),
        }

    registry.register(Capability(
        id="agent.archive_collaborator",
        description=(
            "Soft-delete a named collaborator. The collaborator's "
            "persona and history rows are preserved; recreate with "
            "the same name to start a fresh entity."
        ),
        handler=archive_handler,
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        tier=Tier.WRITE_DESTRUCTIVE,
        scope="collaborator_lifecycle",
    ))

    # ── agent.delegate_to ──────────────────────────────────────────

    def delegate_handler(
        *, name: str, task: str,
        parent_session_id: str = "unknown",
    ) -> dict[str, Any]:
        # Recursion cap (Decision W6-5)
        if _inside_collaborator.get():
            raise CapabilityDenied(
                "agent.delegate_to refuses recursion — collaborators "
                "cannot spawn other collaborators (depth cap = 1)."
            )

        collaborator = get_collaborator_by_name(db, name)
        if collaborator is None:
            return {
                "delegated": False,
                "error": (
                    f"no active collaborator named {name!r}. "
                    "Use agent.create_collaborator first or "
                    "agent.list_collaborators to see what's available."
                ),
            }

        started = time.time()
        try:
            response = _run_collaborator_turn(
                db, write_queue, cfg,
                collaborator, task, parent_session_id,
            )
        except Exception as e:
            return {
                "delegated": True,
                "succeeded": False,
                "collaborator": name,
                "error_class": type(e).__name__,
                "error": str(e)[:500],
                "duration_ms": int((time.time() - started) * 1000),
                "outcome_score": 0.0,
            }

        return {
            "delegated": True,
            "succeeded": True,
            "collaborator": name,
            "result": response,
            "duration_ms": int((time.time() - started) * 1000),
            "outcome_score": 1.0,
        }

    registry.register(Capability(
        id="agent.delegate_to",
        description=(
            "Send a task to a named collaborator and wait for its "
            "result. The collaborator runs end-to-end with its own "
            "filtered memory view and returns a single string. Use "
            "agent.list_collaborators to discover available collaborators."
        ),
        handler=delegate_handler,
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of an existing collaborator.",
                },
                "task": {
                    "type": "string",
                    "description": "What you want the collaborator to do.",
                },
                "parent_session_id": {
                    "type": "string",
                    "description": (
                        "Session id to use for collaborator memory "
                        "scoping. Usually the parent agent's session id."
                    ),
                },
            },
            "required": ["name", "task"],
        },
        tier=Tier.EXTERNAL_EFFECT,
        scope="collaborator_dispatch",
    ))
