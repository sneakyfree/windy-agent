"""/goal Phase 3 — autorun (bounded autonomous loop).

When the user runs ``/goal autorun N``, the worker model iterates
on the active goal for up to N turns without user input, then
delivers a SINGLE summary message at the end. This is the most
ambitious /goal phase; safety lives behind multiple hard caps:

  - **Turn cap**: max ``AUTORUN_MAX_TURNS_HARD_CAP`` (10) regardless
    of what the user requested. No env override.
  - **Token cap**: ``AUTORUN_MAX_TOKENS_PER_RUN`` (50K). Aborts on
    overshoot — cost-overrun protection.
  - **Wall-clock cap**: ``AUTORUN_MAX_WALL_SECONDS`` (5 min). Aborts
    on overshoot — prevents a wedged turn from blocking pacing or
    interactive chat forever.
  - **Per-turn timeout**: each ``agent_respond`` call wrapped in
    ``asyncio.wait_for(60s)``.
  - **User-message cancellation**: channel layer calls ``cancel()``
    on the running task when the user sends anything during the
    autorun — switches the bot back to interactive mode mid-run.
  - **Goal-state cancellation**: if the user runs ``/goal clear``
    or the evaluator marks the goal MET / EXPIRED, the autorun
    aborts on its next iteration check.

Delivery: a SINGLE summary message at the end via the channel-
agnostic ``DeliveryFn`` callback. NO per-turn pings — that's
notification spam.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from windyfly.memory.database import Database

logger = logging.getLogger(__name__)


PER_TURN_TIMEOUT_S = 60.0

# Pinned synthetic prompt for autorun turns. Tells the worker:
# "you're being run autonomously, the user didn't send this, just
# keep advancing the goal." Matches the language Phase 2 pacing
# uses so prompt-cache hit rate stays high across the two paths.
_AUTORUN_PROMPT = (
    "[autorun turn] You are running autonomously on this goal — "
    "the user did NOT send this message; you woke up to take one "
    "more step. Use your tools, surface what you found, and END "
    "this turn with one concrete deliverable or one specific "
    "blocker. Be brief (3-4 sentences). No greetings; no recap of "
    "the goal text. The user is NOT watching; if you finish the "
    "goal, say MET-COMPLETION on a new line so the evaluator "
    "catches it."
)


DeliveryFn = Callable[[str, str], Awaitable[None]]
AgentRespondFn = Callable[..., str]


# Module-level registry of running autorun tasks, keyed by goal_id.
# Allows the channel layer to cancel a running autorun when the user
# sends a message in the same session. Cleared in-place on task
# completion (success or cancel).
_RUNNING_AUTORUNS: dict[str, asyncio.Task[Any]] = {}


def register_autorun_task(goal_id: str, task: asyncio.Task[Any]) -> None:
    """Channel adapter registers each fresh autorun task here so the
    cancellation path can find it by goal_id."""
    _RUNNING_AUTORUNS[goal_id] = task


def cancel_autorun_for_session(db: Database, session_id: str) -> bool:
    """Cancel any active autorun on the active goal for this
    session. Called by the channel layer on incoming user messages
    so the user can interrupt a running autorun by just typing.
    Returns True iff something was actually cancelled.
    """
    from windyfly.memory.goals import get_active_goal, stop_autorun

    active = get_active_goal(db, session_id)
    if not active:
        return False
    task = _RUNNING_AUTORUNS.get(active["id"])
    if not task or task.done():
        return False
    logger.info(
        "cancelling autorun on goal %s — user message arrived",
        active["id"],
    )
    stop_autorun(db, active["id"])
    task.cancel()
    return True


async def run_autorun(
    *,
    goal_id: str,
    db: Database,
    deliver: DeliveryFn,
    agent_respond: AgentRespondFn,
    config: dict[str, Any],
    write_queue: Any,
    tool_registry: Any = None,
) -> dict[str, Any]:
    """Execute the autorun loop until exhaustion / cancellation /
    cap. Delivers a single end-of-run summary; returns a dict the
    test suite can assert on.
    """
    from windyfly.memory import goals as goals_mod

    started_at = time.time()
    goal = goals_mod.get_goal(db, goal_id)
    if not goal:
        return {"status": "no_goal"}
    if not goal.get("chat_id"):
        # Channel layer should always set chat_id when starting
        # autorun, but be defensive.
        return {"status": "no_chat_id"}

    turns_run = 0
    last_reply = ""
    abort_reason: str | None = None
    try:
        while True:
            # Refresh goal state on every iteration so external
            # changes (user /goal clear, evaluator MET) take effect.
            current = goals_mod.get_goal(db, goal_id)
            if not current or current.get("status") != goals_mod.STATUS_ACTIVE:
                abort_reason = (
                    f"goal status changed to "
                    f"{current.get('status') if current else 'gone'}"
                )
                break
            if int(current.get("autorun_remaining") or 0) <= 0:
                abort_reason = "turns exhausted"
                break
            if int(current.get("autorun_tokens_used") or 0) >= goals_mod.AUTORUN_MAX_TOKENS_PER_RUN:
                abort_reason = (
                    f"token cap reached "
                    f"({goals_mod.AUTORUN_MAX_TOKENS_PER_RUN})"
                )
                break
            if time.time() - started_at >= goals_mod.AUTORUN_MAX_WALL_SECONDS:
                abort_reason = (
                    f"wall-clock cap reached "
                    f"({goals_mod.AUTORUN_MAX_WALL_SECONDS}s)"
                )
                break

            loop = asyncio.get_running_loop()
            try:
                reply: str = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: agent_respond(
                            config=config, db=db, write_queue=write_queue,
                            user_message=_AUTORUN_PROMPT,
                            session_id=current["session_id"],
                            tool_registry=tool_registry,
                        ),
                    ),
                    timeout=PER_TURN_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                abort_reason = f"turn exceeded {int(PER_TURN_TIMEOUT_S)}s timeout"
                break
            except Exception as e:
                abort_reason = f"turn raised {type(e).__name__}: {e}"
                break

            last_reply = reply
            turns_run += 1
            # Crude token estimate: 4 chars/token. The real loop
            # records exact tokens to cost_ledger, but for the
            # in-row tally we just want a bounded approximation.
            tokens_est = len(reply) // 4 + len(_AUTORUN_PROMPT) // 4
            goals_mod.decrement_autorun(
                db, goal_id, tokens_used=tokens_est,
            )

            # Quick MET sentinel — worker can self-terminate by
            # printing MET-COMPLETION (mirrors Claude Code's
            # explicit-stop pattern).
            if "MET-COMPLETION" in reply:
                abort_reason = "worker emitted MET-COMPLETION"
                break
    except asyncio.CancelledError:
        abort_reason = "cancelled by user message"
        # re-raise after summary delivery
    finally:
        _RUNNING_AUTORUNS.pop(goal_id, None)
        # Always make sure the DB flag is cleared on exit.
        goals_mod.stop_autorun(db, goal_id)

    elapsed = int(time.time() - started_at)
    final_goal = goals_mod.get_goal(db, goal_id) or {}
    summary = _format_summary(
        goal_text=goal["text"],
        turns_run=turns_run,
        elapsed_s=elapsed,
        tokens_est=int(final_goal.get("autorun_tokens_used") or 0),
        last_reply=last_reply,
        abort_reason=abort_reason or "ok",
        final_status=str(final_goal.get("status") or "active"),
    )
    try:
        await deliver(goal["chat_id"], summary)
    except Exception as e:
        logger.warning("autorun summary delivery failed: %s", e)

    return {
        "status": "ok",
        "turns_run": turns_run,
        "elapsed_s": elapsed,
        "abort_reason": abort_reason,
        "final_goal_status": str(final_goal.get("status") or "active"),
    }


def _format_summary(
    *,
    goal_text: str,
    turns_run: int,
    elapsed_s: int,
    tokens_est: int,
    last_reply: str,
    abort_reason: str,
    final_status: str,
) -> str:
    """Render the end-of-autorun message for the user. Short header
    + last-reply excerpt + outcome line. No per-turn dump."""
    elapsed_str = (
        f"{elapsed_s // 60}m {elapsed_s % 60}s"
        if elapsed_s >= 60 else f"{elapsed_s}s"
    )
    excerpt = last_reply.strip()[:1200]
    status_emoji = "✅" if final_status == "completed" else "⏸"
    return (
        f"{status_emoji} *Autorun complete*\n\n"
        f"> {goal_text[:200]}\n\n"
        f"*Turns:* {turns_run}  *Elapsed:* {elapsed_str}  "
        f"*Tokens:* ~{tokens_est:,}\n"
        f"*Outcome:* {abort_reason}\n\n"
        f"*Last update:*\n{excerpt}"
    )
