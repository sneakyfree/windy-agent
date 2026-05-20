"""Goals — session-scoped persistent objectives.

Implements the ``/goal <text>`` slash-command surface (windy-agent
feature parity with Claude Code 2.1.139's ``/goal`` and the
identical pattern in Codex CLI / Hermes Agent 0.13.0).

Design notes worth keeping:

- **One active goal per session.** The session is the unit of
  identity for "what is this conversation about right now."
  Setting a new goal while one is active completes the old one
  with status='abandoned' first — no overlapping goals.
- **Evaluator history is JSON in-row, not a sibling table.** A
  goal's evaluator log is typically <50 entries and read together
  with the goal; the join cost of a sibling table outweighed the
  cleanliness. Append-only list keeps it bounded.
- **Auto-expiry is the evaluator's job, not a cron.** When the
  ``goal_evaluator`` returns ``"unrelated"`` for the third turn
  in a row, the loop calls ``expire_goal`` rather than relying on
  a background sweeper. This way the user's screen always shows a
  state consistent with the conversation right above it.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from windyfly.memory.database import Database


# Status enum (string-typed to keep SQL simple and the values
# self-documenting in raw DB inspection).
STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"
STATUS_EXPIRED = "expired"

# Verdict shapes the evaluator returns. Kept in this module rather
# than goal_evaluator.py so storage and judgment share a single
# vocabulary and the lint cross-checks both.
VERDICT_MET = "met"
VERDICT_ADVANCED = "advanced"
VERDICT_BLOCKED = "blocked"
VERDICT_UNRELATED = "unrelated"

# Threshold for the auto-expire heuristic. Three consecutive
# "unrelated" verdicts means the user has clearly moved on — keep
# pinning the old goal onto every system prompt at that point is
# noise that crowds out real context.
AUTO_EXPIRE_AFTER_CONSECUTIVE_UNRELATED = 3

# How many evaluator-history entries we'll keep inline before
# pruning the oldest. Goals lasting hundreds of turns are real;
# letting the JSON grow unbounded isn't.
MAX_EVAL_HISTORY = 50


def create_goal(
    db: Database,
    *,
    session_id: str,
    text: str,
    user_id: str = "default",
    evaluator_model: str | None = None,
) -> str:
    """Set a new active goal for the session.

    If an active goal already exists in this session, mark it
    ``abandoned`` first — one active goal per session, by design.

    Returns the new goal id.
    """
    text = text.strip()
    if not text:
        raise ValueError("goal text cannot be empty")

    # Cap pathologically long goals so a paste-bomb doesn't blow up
    # the prompt block. 800 chars is plenty for a real objective.
    if len(text) > 800:
        text = text[:797].rstrip() + "..."

    existing = get_active_goal(db, session_id, user_id=user_id)
    if existing:
        db.execute(
            "UPDATE goals SET status = ?, completed_at = CURRENT_TIMESTAMP, "
            "closing_note = ? WHERE id = ?",
            (STATUS_ABANDONED,
             "replaced by a new /goal before completion",
             existing["id"]),
        )

    goal_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO goals (id, session_id, user_id, text, status, "
        "evaluator_model) VALUES (?, ?, ?, ?, ?, ?)",
        (goal_id, session_id, user_id, text, STATUS_ACTIVE, evaluator_model),
    )
    db.commit()
    return goal_id


def get_active_goal(
    db: Database,
    session_id: str,
    user_id: str = "default",
) -> dict[str, Any] | None:
    """The current active goal for this session, or None."""
    return db.fetchone(
        "SELECT * FROM goals WHERE session_id = ? AND user_id = ? "
        "AND status = ? ORDER BY created_at DESC LIMIT 1",
        (session_id, user_id, STATUS_ACTIVE),
    )


def get_goal(db: Database, goal_id: str) -> dict[str, Any] | None:
    """Lookup by id; used by the evaluator + audit dashboards."""
    return db.fetchone("SELECT * FROM goals WHERE id = ?", (goal_id,))


def list_goals(
    db: Database,
    *,
    session_id: str | None = None,
    user_id: str = "default",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Recent goals for a session or user. Used by /goal status to
    show completed history when there's no active goal."""
    if session_id:
        return db.fetchall(
            "SELECT * FROM goals WHERE session_id = ? AND user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, user_id, limit),
        )
    return db.fetchall(
        "SELECT * FROM goals WHERE user_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )


def record_turn(
    db: Database,
    goal_id: str,
    *,
    tokens_input: int = 0,
    tokens_output: int = 0,
) -> None:
    """Bump the turn count + token tallies for a goal. Called once
    per assistant reply that fired with this goal active."""
    db.execute(
        "UPDATE goals SET turns_count = turns_count + 1, "
        "tokens_input = tokens_input + ?, "
        "tokens_output = tokens_output + ? WHERE id = ?",
        (tokens_input, tokens_output, goal_id),
    )
    db.commit()


def record_evaluation(
    db: Database,
    goal_id: str,
    *,
    verdict: str,
    reason: str,
    progress_note: str | None = None,
    turn: int | None = None,
) -> None:
    """Append an evaluator verdict to the goal's history and update
    the consecutive_unrelated counter (which drives auto-expire).

    The history is bounded at MAX_EVAL_HISTORY by drop-oldest
    pruning so a 500-turn goal doesn't fill the row with JSON.
    """
    if verdict not in (VERDICT_MET, VERDICT_ADVANCED, VERDICT_BLOCKED, VERDICT_UNRELATED):
        raise ValueError(f"invalid verdict {verdict!r}")

    goal = get_goal(db, goal_id)
    if not goal:
        return

    history_raw = goal.get("evaluator_history") or "[]"
    try:
        history = json.loads(history_raw)
        if not isinstance(history, list):
            history = []
    except (json.JSONDecodeError, TypeError):
        history = []

    entry: dict[str, Any] = {"verdict": verdict, "reason": reason}
    if progress_note:
        entry["progress_note"] = progress_note
    if turn is not None:
        entry["turn"] = turn
    history.append(entry)
    if len(history) > MAX_EVAL_HISTORY:
        history = history[-MAX_EVAL_HISTORY:]

    if verdict == VERDICT_UNRELATED:
        new_consec = int(goal.get("consecutive_unrelated") or 0) + 1
    else:
        new_consec = 0

    db.execute(
        "UPDATE goals SET evaluator_history = ?, "
        "consecutive_unrelated = ? WHERE id = ?",
        (json.dumps(history), new_consec, goal_id),
    )
    db.commit()


def complete_goal(
    db: Database,
    goal_id: str,
    *,
    closing_note: str | None = None,
) -> None:
    """Mark a goal completed — set by either ``/goal done`` or by
    the evaluator returning ``met``. Closing note appears in the
    user-facing announcement."""
    db.execute(
        "UPDATE goals SET status = ?, completed_at = CURRENT_TIMESTAMP, "
        "closing_note = ? WHERE id = ?",
        (STATUS_COMPLETED, closing_note, goal_id),
    )
    db.commit()


def abandon_goal(
    db: Database,
    goal_id: str,
    *,
    closing_note: str | None = None,
) -> None:
    """User-driven /goal clear path. Distinct from ``expire_goal``
    so dashboards can tell intentional abandonment apart from the
    'user moved on without saying so' auto-expire path."""
    db.execute(
        "UPDATE goals SET status = ?, completed_at = CURRENT_TIMESTAMP, "
        "closing_note = ? WHERE id = ?",
        (STATUS_ABANDONED, closing_note, goal_id),
    )
    db.commit()


def expire_goal(db: Database, goal_id: str) -> None:
    """Auto-expire path triggered when the evaluator returns
    ``unrelated`` for ``AUTO_EXPIRE_AFTER_CONSECUTIVE_UNRELATED``
    turns in a row. Closing note is filled in automatically so the
    user can audit what happened."""
    db.execute(
        "UPDATE goals SET status = ?, completed_at = CURRENT_TIMESTAMP, "
        "closing_note = ? WHERE id = ?",
        (STATUS_EXPIRED,
         f"auto-expired after {AUTO_EXPIRE_AFTER_CONSECUTIVE_UNRELATED} "
         "consecutive unrelated turns",
         goal_id),
    )
    db.commit()


# ── Phase 2: timer-driven pacing ──────────────────────────────────
#
# Pacing lets a user opt a goal into scheduled "progress check"
# nudges: "/goal pace 4h" tells the bot to wake up every 4 hours,
# make one concrete advance on the goal, and ping the user with the
# result. Off by default. One pace cadence per goal (one goal per
# session, so effectively per-session).
#
# Anti-spam guards live here, not in the scheduler — easier to test
# and audit when the rules co-locate with the storage:
#   - Quiet hours (configurable, defaults 23:00-07:00 local) skip fire
#   - Recent-user-activity threshold (don't nudge if user just spoke)
#   - Ignored-fire counter auto-pauses after N consecutive misses

MIN_PACE_SECONDS = 5 * 60         # 5 minutes — anything less is spam
MAX_PACE_SECONDS = 24 * 60 * 60   # 24 hours — anything more isn't pacing
AUTO_PAUSE_AFTER_IGNORED = 3      # consecutive ignored fires → pace off


def set_goal_pace(
    db: Database,
    goal_id: str,
    *,
    pace_seconds: int,
    chat_id: str | None = None,
) -> None:
    """Set or update the pacing cadence for a goal.

    pace_seconds == 0 disables pacing. Validated against MIN/MAX
    range otherwise. chat_id is captured at this call so the
    scheduler knows where to deliver; if not provided, prior
    chat_id (if any) is preserved.
    """
    if pace_seconds < 0:
        raise ValueError(f"pace_seconds must be >= 0, got {pace_seconds}")
    if 0 < pace_seconds < MIN_PACE_SECONDS:
        raise ValueError(
            f"pace_seconds must be >= {MIN_PACE_SECONDS} (5 minutes); "
            f"got {pace_seconds}"
        )
    if pace_seconds > MAX_PACE_SECONDS:
        raise ValueError(
            f"pace_seconds must be <= {MAX_PACE_SECONDS} (24 hours); "
            f"got {pace_seconds}"
        )
    if chat_id is not None:
        db.execute(
            "UPDATE goals SET pace_seconds = ?, chat_id = ?, "
            "ignored_pace_fires = 0 WHERE id = ?",
            (pace_seconds, chat_id, goal_id),
        )
    else:
        db.execute(
            "UPDATE goals SET pace_seconds = ?, "
            "ignored_pace_fires = 0 WHERE id = ?",
            (pace_seconds, goal_id),
        )
    db.commit()


def mark_paced(db: Database, goal_id: str, *, fired: bool = True) -> None:
    """Update last_paced_at when a scheduled fire occurs. When
    ``fired`` is True the timestamp is set to now; when False the
    counter is bumped (used to record skipped fires)."""
    if fired:
        db.execute(
            "UPDATE goals SET last_paced_at = CURRENT_TIMESTAMP WHERE id = ?",
            (goal_id,),
        )
    db.commit()


def bump_ignored_fires(db: Database, goal_id: str) -> int:
    """Increment ignored_pace_fires; return new count. Caller pauses
    pacing once count >= AUTO_PAUSE_AFTER_IGNORED."""
    db.execute(
        "UPDATE goals SET ignored_pace_fires = ignored_pace_fires + 1 "
        "WHERE id = ?",
        (goal_id,),
    )
    db.commit()
    row = db.fetchone(
        "SELECT ignored_pace_fires AS n FROM goals WHERE id = ?",
        (goal_id,),
    )
    return int((row or {}).get("n", 0))


def reset_ignored_fires(db: Database, goal_id: str) -> None:
    """Called when the user replies after a paced fire — they're
    engaged, reset the auto-pause counter."""
    db.execute(
        "UPDATE goals SET ignored_pace_fires = 0 WHERE id = ?",
        (goal_id,),
    )
    db.commit()


def goals_due_for_pacing(db: Database) -> list[dict[str, Any]]:
    """Active goals whose ``pace_seconds`` has elapsed since their
    ``last_paced_at`` (or since ``created_at`` if never paced).
    Used by the scheduler loop to find work each tick.
    """
    return db.fetchall(
        "SELECT *, "
        "  CAST((julianday('now') - "
        "        julianday(COALESCE(last_paced_at, created_at))) * 86400 "
        "       AS INTEGER) AS seconds_since "
        "FROM goals "
        "WHERE status = 'active' AND pace_seconds > 0 "
        "  AND CAST((julianday('now') - "
        "            julianday(COALESCE(last_paced_at, created_at))) * 86400 "
        "           AS INTEGER) >= pace_seconds",
    )


# ── Phase 3: autorun (bounded autonomous loop) ─────────────────────
#
# /goal autorun N runs the agent for up to N turns without user
# input, then delivers a single summary message. Off by default;
# explicit opt-in. Hard caps live here in storage to keep them
# audit-friendly and uniformly enforced regardless of caller.
#
# The orchestrator that consumes these helpers lives in
# ``agent/goal_autorun.py``.

AUTORUN_MAX_TURNS_HARD_CAP = 10        # cannot be overridden
AUTORUN_MAX_TOKENS_PER_RUN = 50_000    # cost-overrun protection
AUTORUN_MAX_WALL_SECONDS = 5 * 60      # 5 minutes wall-clock cap


def start_autorun(
    db: Database,
    goal_id: str,
    *,
    max_turns: int,
    chat_id: str | None = None,
) -> int:
    """Begin an autorun on the goal. Returns the effective
    max_turns (clamped to AUTORUN_MAX_TURNS_HARD_CAP).

    Idempotent in spirit: calling on an already-running autorun
    REPLACES it with the new max_turns (use case: user wants more
    turns mid-run). chat_id is captured for the summary delivery.
    """
    if max_turns < 1:
        raise ValueError(f"max_turns must be >= 1, got {max_turns}")
    capped = min(max_turns, AUTORUN_MAX_TURNS_HARD_CAP)
    if chat_id is not None:
        db.execute(
            "UPDATE goals SET autorun_remaining = ?, autorun_max_turns = ?, "
            "autorun_started_at = CURRENT_TIMESTAMP, "
            "autorun_tokens_used = 0, chat_id = ? WHERE id = ?",
            (capped, capped, chat_id, goal_id),
        )
    else:
        db.execute(
            "UPDATE goals SET autorun_remaining = ?, autorun_max_turns = ?, "
            "autorun_started_at = CURRENT_TIMESTAMP, "
            "autorun_tokens_used = 0 WHERE id = ?",
            (capped, capped, goal_id),
        )
    db.commit()
    return capped


def decrement_autorun(
    db: Database,
    goal_id: str,
    *,
    tokens_used: int = 0,
) -> int:
    """Decrement remaining turns + accumulate token usage. Returns
    the new ``autorun_remaining``. The orchestrator calls this
    AFTER each turn so the count reflects "turns left to run."
    """
    db.execute(
        "UPDATE goals SET autorun_remaining = MAX(0, autorun_remaining - 1), "
        "autorun_tokens_used = autorun_tokens_used + ? WHERE id = ?",
        (tokens_used, goal_id),
    )
    db.commit()
    row = db.fetchone(
        "SELECT autorun_remaining AS n FROM goals WHERE id = ?", (goal_id,),
    )
    return int((row or {}).get("n", 0))


def stop_autorun(db: Database, goal_id: str) -> None:
    """Force-stop the autorun on a goal. Called by:
      - User typing ``/goal autorun stop``
      - Channel detecting user message during autorun (cancellation)
      - Cost/wall-clock/turn caps tripping
    """
    db.execute(
        "UPDATE goals SET autorun_remaining = 0 WHERE id = ?",
        (goal_id,),
    )
    db.commit()


def get_progress_notes(goal: dict[str, Any], limit: int = 5) -> list[str]:
    """Pull the last ``limit`` progress notes out of the evaluator
    history for the /goal status reply. Returns most-recent-first."""
    history_raw = goal.get("evaluator_history") or "[]"
    try:
        history = json.loads(history_raw)
    except (json.JSONDecodeError, TypeError):
        return []
    notes: list[str] = []
    for entry in reversed(history):
        note = entry.get("progress_note")
        if note:
            notes.append(note)
            if len(notes) >= limit:
                break
    return notes
