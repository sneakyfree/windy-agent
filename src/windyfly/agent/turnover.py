"""Turnover letters — what survives a /new session reset.

The prompt assembler has loaded "## Last Session Handoff" from the most
recent ``turnover_letter`` node since the feature shipped — but nothing
in the codebase ever WROTE one (2026-07-04 audit: dead socket). This is
the writer.

Design (deliberate):

- **Deterministic, no LLM call.** /new must never fail or stall on a
  model round-trip — session reset is a rescue path. A cheap extractive
  digest beats a beautiful summary that can time out.
- **Bounded.** The letter is hard-capped (~1,200 chars). The Hermes
  lesson: a small, always-loaded handoff forces prioritization and
  can't bloat the next session's context.
- **One letter per channel, updated in place.** ``upsert_node`` keys on
  (type, name, scope), so letters never accumulate; the reader's
  ``limit=1`` gets the freshest handoff.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

MAX_LETTER_CHARS = 1200
_EPISODES_TO_SCAN = 14
_PREVIEW_CHARS = 110


def compose_turnover_summary(
    db: Any,
    session_id: str,
) -> str | None:
    """Extractive digest of the ending session. None if nothing to say."""
    from windyfly.memory.episodes import get_recent_episodes

    episodes = get_recent_episodes(
        db, limit=_EPISODES_TO_SCAN, session_id=session_id,
    )
    if not episodes:
        return None

    # Most-recent-first from the DB; chronological for the digest.
    episodes = list(reversed(episodes))

    user_lines = [
        (e.get("content") or "").strip().replace("\n", " ")
        for e in episodes
        if e.get("role") == "user"
    ]
    topics = [f"• {u[:_PREVIEW_CHARS]}" for u in user_lines[-5:] if u]

    last_exchange = ""
    for e in reversed(episodes):
        if e.get("role") == "assistant":
            last_exchange = (
                (e.get("content") or "").strip().replace("\n", " ")
            )[:_PREVIEW_CHARS * 2]
            break

    parts: list[str] = []
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts.append(f"Previous session ended {when} ({len(episodes)} recent turns).")

    try:
        from windyfly.memory.goals import get_active_goal
        goal = get_active_goal(db, session_id=session_id)
        if goal and goal.get("description"):
            parts.append(f"Active goal (carrying over): {goal['description'][:200]}")
    except Exception:
        pass

    if topics:
        parts.append("The user was recently asking about:\n" + "\n".join(topics))
    if last_exchange:
        parts.append(f"My last reply (gist): {last_exchange}")
    parts.append(
        "If the user references 'that thing we were doing', it likely "
        "means one of the topics above."
    )

    summary = "\n".join(parts)
    return summary[:MAX_LETTER_CHARS]


def write_turnover_letter(
    db: Any,
    write_queue: Any | None,
    *,
    platform: str,
    channel_id: str,
    session_id: str,
) -> bool:
    """Compose + persist the handoff for the session being reset.

    Best-effort by contract: /new must succeed even if this fails.
    Returns True when a letter was written.
    """
    try:
        summary = compose_turnover_summary(db, session_id)
        if not summary:
            return False
        from windyfly.memory.nodes import upsert_node

        upsert_node(
            db,
            type="turnover_letter",
            name=f"turnover:{platform}:{channel_id}",
            metadata={
                "summary": summary,
                "session_id": session_id,
                "written_at": datetime.now(timezone.utc).isoformat(),
            },
            epistemic_status="verified",
            confidence=1.0,
            source="session_reset",
        )
        logger.info(
            "turnover letter written for %s:%s (%d chars)",
            platform, channel_id, len(summary),
        )
        return True
    except Exception as e:
        logger.warning("turnover letter write failed (non-fatal): %s", e)
        return False
