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
