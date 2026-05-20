"""/goal Phase 2 — timer-driven scheduled progress nudges.

When the user runs ``/goal pace 4h``, this scheduler wakes every
60 seconds, scans for goals whose pacing interval has elapsed, and
fires a synthetic "scheduled progress check" turn for each due
goal. The bot's reply lands on the user's Telegram chat as a
proactive ping.

Design notes worth keeping:

- **Single class-level loop, not per-goal asyncio task.** Per-goal
  tasks are easier to reason about in isolation but harder to
  bound: a leaked task with a flaky goal can spin forever. One
  scheduler that re-scans every tick is simpler, cheaper at scale,
  and trivially observable ("is the loop alive?" is one task).
- **Anti-spam guards live in memory/goals.py** (MIN_PACE_SECONDS,
  AUTO_PAUSE_AFTER_IGNORED), not here — easier to test in
  isolation and harder to forget when adding new delivery channels.
- **Quiet hours.** No pacing fires between 23:00 and 07:00 local
  time. Grandma is asleep. Configurable per goal in a future
  iteration; today it's a hard rule.
- **Recent-activity skip.** If the user has sent a message in the
  last ``pace_seconds / 4`` seconds, skip this fire — they're
  actively engaged, no need to interrupt.
- **Auto-pause on ignored.** After 3 consecutive scheduled fires
  with no user response, set ``pace_seconds = 0`` so the goal
  stops pinging. User can re-enable with ``/goal pace <duration>``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from windyfly.memory.database import Database

logger = logging.getLogger(__name__)


# Quiet hours — local time. Outside this window pacing is silent.
# (Grandma sleeps; bot doesn't poke her at 3am.) Override via env
# for fleet machines in non-residential timezones.
QUIET_HOURS_START = int(os.environ.get("WINDY_GOAL_QUIET_START", "23"))
QUIET_HOURS_END = int(os.environ.get("WINDY_GOAL_QUIET_END", "7"))

# How often the scheduler wakes to scan for due goals. Doesn't need
# to be precise — pace_seconds is the real cadence; this is just
# the polling granularity. 60s keeps overhead negligible.
SCHEDULER_TICK_SECONDS = int(os.environ.get("WINDY_GOAL_PACE_TICK", "60"))


# Type alias for the delivery callback the channel adapter provides.
# Takes (chat_id, message_text) and resolves when sent. Lets us
# stay channel-agnostic — Telegram is the only consumer today, but
# Matrix/SMS could plug in identically.
DeliveryFn = Callable[[str, str], Awaitable[None]]

# Type alias for the agent-respond callback (sync, called via
# run_in_executor since agent_respond is sync).
AgentRespondFn = Callable[..., str]


def in_quiet_hours(now: datetime | None = None) -> bool:
    """True iff the current local hour is in the configured quiet
    window. Handles wraparound (e.g., 23→07 spans midnight)."""
    h = (now or datetime.now()).hour
    if QUIET_HOURS_START < QUIET_HOURS_END:
        return QUIET_HOURS_START <= h < QUIET_HOURS_END
    # Wraparound window
    return h >= QUIET_HOURS_START or h < QUIET_HOURS_END


def user_recently_active(
    db: Database,
    session_id: str,
    *,
    threshold_seconds: int,
) -> bool:
    """True iff the user has sent a message in this session within
    ``threshold_seconds``. Skip pacing in that case — they're
    engaged, no need for proactive nudges."""
    row = db.fetchone(
        "SELECT CAST((julianday('now') - julianday(MAX(created_at))) * 86400 "
        "       AS INTEGER) AS age "
        "FROM episodes WHERE session_id = ? AND role = 'user'",
        (session_id,),
    )
    if not row or row.get("age") is None:
        return False
    return int(row["age"]) < threshold_seconds


async def _fire_progress_check(
    *,
    goal: dict[str, Any],
    db: Database,
    deliver: DeliveryFn,
    agent_respond: AgentRespondFn,
    config: dict[str, Any],
    write_queue: Any,
    tool_registry: Any = None,
) -> None:
    """Fire one synthetic progress-check turn and deliver the bot's
    reply to the chat. Anti-spam guards already evaluated by caller;
    this just runs the turn.
    """
    from windyfly.memory import goals as goals_mod

    synthetic_prompt = (
        "[scheduled progress check] Make one concrete advance on the "
        "active goal — use your tools, surface what you found, or "
        "ask one targeted question if truly blocked. Be brief (2-3 "
        "sentences). The user did NOT just send this; you woke up "
        "on schedule. No greetings, no recap of the goal text."
    )
    loop = asyncio.get_running_loop()
    try:
        reply: str = await loop.run_in_executor(
            None,
            lambda: agent_respond(
                config=config, db=db, write_queue=write_queue,
                user_message=synthetic_prompt,
                session_id=goal["session_id"],
                tool_registry=tool_registry,
            ),
        )
    except Exception as e:
        logger.warning("paced progress-check raised for goal %s: %s",
                       goal["id"], e)
        return

    # Prefix the proactive ping so the user knows this wasn't a
    # reply to something they just sent — grandma sees "🎯 Hey,
    # quick goal update:" and understands the bot woke itself up.
    text = f"🎯 *Quick goal update:*\n\n{reply}"
    try:
        await deliver(goal["chat_id"], text)
    except Exception as e:
        logger.warning("paced delivery failed for goal %s: %s",
                       goal["id"], e)
        return

    # Record the fire timestamp + bump the ignored-counter
    # speculatively. The counter resets when the user sends their
    # next message in this session (handled by the channel layer
    # calling reset_ignored_fires on incoming user activity).
    goals_mod.mark_paced(db, goal["id"], fired=True)
    new_ignored = goals_mod.bump_ignored_fires(db, goal["id"])
    if new_ignored >= goals_mod.AUTO_PAUSE_AFTER_IGNORED:
        logger.info(
            "auto-pausing pacing on goal %s (ignored %d fires)",
            goal["id"], new_ignored,
        )
        goals_mod.set_goal_pace(db, goal["id"], pace_seconds=0)


async def _scheduler_tick(
    *,
    db: Database,
    deliver: DeliveryFn,
    agent_respond: AgentRespondFn,
    config: dict[str, Any],
    write_queue: Any,
    tool_registry: Any = None,
) -> int:
    """One pass over due goals. Returns count of fires actually
    executed (post-guards). Exposed separately for testability."""
    from windyfly.memory import goals as goals_mod

    if in_quiet_hours():
        return 0

    due = goals_mod.goals_due_for_pacing(db)
    fired = 0
    for goal in due:
        # Skip if no chat_id (delivery target unknown — e.g.,
        # /goal pace was never called even though pace_seconds
        # somehow >0; defensive)
        if not goal.get("chat_id"):
            logger.warning("goal %s paced without chat_id, skipping",
                           goal["id"])
            continue
        # Recent-activity skip: don't nudge if user has been
        # active in the last quarter of the pacing window.
        activity_threshold = max(60, int(goal["pace_seconds"]) // 4)
        if user_recently_active(
            db, goal["session_id"],
            threshold_seconds=activity_threshold,
        ):
            logger.debug(
                "goal %s pacing skipped — user active in last %ds",
                goal["id"], activity_threshold,
            )
            continue
        await _fire_progress_check(
            goal=goal, db=db, deliver=deliver,
            agent_respond=agent_respond, config=config,
            write_queue=write_queue, tool_registry=tool_registry,
        )
        fired += 1
    return fired


async def scheduler_loop(
    *,
    db: Database,
    deliver: DeliveryFn,
    agent_respond: AgentRespondFn,
    config: dict[str, Any],
    write_queue: Any,
    tool_registry: Any = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Long-running scheduler. Channel adapter starts this as a
    background task on connect, awaits stop_event for shutdown.

    Logs at INFO on each fire, DEBUG on skips. Never raises — wraps
    each tick so a transient DB hiccup doesn't kill pacing for the
    whole bot lifetime.
    """
    stop_event = stop_event or asyncio.Event()
    logger.info("goal-pacing scheduler started (tick=%ds)",
                SCHEDULER_TICK_SECONDS)
    while not stop_event.is_set():
        try:
            n_fired = await _scheduler_tick(
                db=db, deliver=deliver,
                agent_respond=agent_respond, config=config,
                write_queue=write_queue, tool_registry=tool_registry,
            )
            if n_fired:
                logger.info("paced %d goal(s) this tick", n_fired)
        except Exception as e:
            logger.warning("scheduler tick errored: %s", e)
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=SCHEDULER_TICK_SECONDS,
            )
        except asyncio.TimeoutError:
            continue
    logger.info("goal-pacing scheduler stopped")
