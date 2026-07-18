"""memory.* capabilities — the agent's key to its own past.

Chronicle Doctrine MUST-BUILD #2 (2026-07-18). The hybrid retrieval
engine (FTS5 + embedding cosine, RRF-fused) has existed in
``memory/episodes.py`` since Sprint 3 — but only the prompt assembler
could use it (push-side). The model itself had no way to actively
interrogate its own history, so "we discussed this three weeks ago"
worked only when the assembler's keyword guess happened to pull the
right episodes forward.

These two capabilities are Grant's two proven manual techniques from
five months in the wild, handed to the model as dumb tools:

- ``memory.search`` — "find when we discussed the Christmas party."
  The index card: hybrid search over every episode ever recorded,
  each hit returned with a ±2-turn window so the surrounding
  conversation gives it meaning.
- ``memory.read_range`` — "go read our last three hours." The page
  turner: raw chronological episodes for a time window, verbatim.
  Grant's #1 context-refresh technique — better than any turnover
  machinery when the human can point at the era that matters.

Both pass the doctrine razor: they RETRIEVE recorded fact. Neither
summarizes, filters by "importance," nor decides what matters — the
model reading the results does the judging. Neither Hermes nor
OpenClaw gives the model this key unprompted.

Band: Tier.READ_EXTERNAL default (USER+). A paired user may search the
shared past; a SANDBOX stranger may not read the owner's life.
"""

from __future__ import annotations

import logging
from typing import Any

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)

_SNIPPET_CHARS = 300
_NEIGHBOR_CHARS = 200
_MAX_SEARCH_HITS = 12
_RANGE_DEFAULT_TURNS = 120
_RANGE_MAX_TURNS = 400
_RANGE_TURN_CHARS = 1500


def _clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def register_memory_search_capabilities(
    registry: CapabilityRegistry,
    db: Any,
    config: dict[str, Any] | None = None,
) -> None:
    """Register memory.search + memory.read_range."""
    logger.info("Registering memory.* capabilities (chronicle retrieval)")

    def memory_search(*, query: str, limit: int = 8) -> dict[str, Any]:
        from windyfly.memory.episodes import search_episodes_hybrid

        query = (query or "").strip()
        if not query:
            return {"ok": False, "error": "query is required"}
        limit = max(1, min(int(limit), _MAX_SEARCH_HITS))

        hits = search_episodes_hybrid(db, query, limit=limit)
        results: list[dict[str, Any]] = []
        for hit in hits:
            item: dict[str, Any] = {
                "when": hit.get("created_at"),
                "session": hit.get("session_id"),
                "role": hit.get("role"),
                "content": _clip(hit.get("content"), _SNIPPET_CHARS),
            }
            # ±2-turn window: the surrounding exchange gives the hit
            # its meaning (a lone "yes, $5 sounds right" is useless
            # without the question above it).
            try:
                before = db.fetchall(
                    "SELECT role, content FROM episodes "
                    "WHERE session_id = ? AND created_at <= ? AND id != ? "
                    "ORDER BY created_at DESC LIMIT 2",
                    (hit.get("session_id"), hit.get("created_at"),
                     hit.get("id")),
                )
                after = db.fetchall(
                    "SELECT role, content FROM episodes "
                    "WHERE session_id = ? AND created_at >= ? AND id != ? "
                    "ORDER BY created_at ASC LIMIT 2",
                    (hit.get("session_id"), hit.get("created_at"),
                     hit.get("id")),
                )
                window = [
                    f"{r['role']}: {_clip(r['content'], _NEIGHBOR_CHARS)}"
                    for r in reversed(list(before or []))
                ] + [
                    f"{r['role']}: {_clip(r['content'], _NEIGHBOR_CHARS)}"
                    for r in (after or [])
                ]
                if window:
                    item["surrounding"] = window
            except Exception:  # noqa: BLE001 — window is best-effort
                pass
            results.append(item)

        return {
            "ok": True,
            "query": query,
            "count": len(results),
            "results": results,
            "hint": (
                "To read a stretch verbatim around a hit, call "
                "memory.read_range with its timestamp."
                if results else
                "No matches. Try different words — the search covers "
                "every conversation ever recorded."
            ),
        }

    def memory_read_range(
        *,
        hours_back: float | None = None,
        start: str | None = None,
        end: str | None = None,
        max_turns: int = _RANGE_DEFAULT_TURNS,
    ) -> dict[str, Any]:
        max_turns = max(1, min(int(max_turns), _RANGE_MAX_TURNS))

        if hours_back is not None:
            try:
                hours = float(hours_back)
            except (TypeError, ValueError):
                return {"ok": False, "error": "hours_back must be a number"}
            if hours <= 0:
                return {"ok": False, "error": "hours_back must be positive"}
            where = "created_at >= datetime('now', ?)"
            params: tuple[Any, ...] = (f"-{hours} hours",)
        elif start:
            if end:
                where = "created_at >= ? AND created_at <= ?"
                params = (start, end)
            else:
                where = "created_at >= ?"
                params = (start,)
        else:
            return {
                "ok": False,
                "error": "pass hours_back (e.g. 3) OR start/end timestamps",
            }

        rows = db.fetchall(
            "SELECT role, content, session_id, created_at FROM episodes "  # noqa: S608
            f"WHERE {where} ORDER BY created_at ASC LIMIT ?",
            (*params, max_turns + 1),
        ) or []

        truncated = len(rows) > max_turns
        rows = rows[:max_turns]
        turns = [
            {
                "when": r["created_at"],
                "role": r["role"],
                "content": _clip(r["content"], _RANGE_TURN_CHARS),
            }
            for r in rows
        ]
        out: dict[str, Any] = {
            "ok": True,
            "count": len(turns),
            "turns": turns,
        }
        if truncated:
            out["truncated"] = True
            out["hint"] = (
                f"More than {max_turns} turns in this window — showing the "
                "earliest. Narrow the range (or raise max_turns, up to "
                f"{_RANGE_MAX_TURNS}) and call again for the rest."
            )
        return out

    registry.register(Capability(
        id="memory.search",
        description=(
            "Search EVERY conversation you and the user have ever had "
            "(keyword + meaning, all sessions, all time). THE tool for "
            "'we talked about this before', 'what did we decide about "
            "X', 'find when we discussed the Christmas party', or any "
            "moment you suspect the answer lives in your shared past "
            "but isn't in your current context. Each hit comes with "
            "the surrounding turns. Don't guess or say you don't "
            "remember — search."
        ),
        handler=memory_search,
        tier=Tier.READ_EXTERNAL,
        scope="memory",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for — names, topics, phrases.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max hits (default 8, cap {_MAX_SEARCH_HITS}).",
                },
            },
            "required": ["query"],
        },
    ))

    registry.register(Capability(
        id="memory.read_range",
        description=(
            "Read the raw, verbatim conversation record for a time "
            "window, in order. THE tool for catching up after a reset "
            "or absence: 'go read our last three hours' → hours_back=3; "
            "'read everything since Thursday afternoon' → start "
            "timestamp. Returns the actual words, not summaries. Use "
            "memory.search first when you don't know WHEN — use this "
            "when you do."
        ),
        handler=memory_read_range,
        tier=Tier.READ_EXTERNAL,
        scope="memory",
        input_schema={
            "type": "object",
            "properties": {
                "hours_back": {
                    "type": "number",
                    "description": "Read the last N hours (e.g. 3).",
                },
                "start": {
                    "type": "string",
                    "description": "ISO timestamp lower bound (alternative to hours_back).",
                },
                "end": {
                    "type": "string",
                    "description": "ISO timestamp upper bound (optional, with start).",
                },
                "max_turns": {
                    "type": "integer",
                    "description": f"Cap on turns returned (default {_RANGE_DEFAULT_TURNS}, max {_RANGE_MAX_TURNS}).",
                },
            },
            "required": [],
        },
    ))
