"""In-process maintenance scheduler (Tier 2 of the cross-platform supervisor).

The 9 systemd timers that ran the agent's periodic self-maintenance
(journal, fire drill, continuity battery, weekly brief, health purge…)
were Linux-only — a Windows or Mac self-hoster got none of them. But a
RUNNING process can schedule its own periodic work; only *restarting a
dead process* needs the OS. So this moves periodic maintenance INTO the
agent, cross-platform for free, and lets the systemd timer zoo be
retired.

Design (dumb + crash-safe):
  - Each job = (name, run_fn, due_fn). due_fn(last_run, now) → bool.
  - Last-run timestamps persist to ONE JSON file in the shared state
    dir. This gives two things at once: (1) crash-idempotence (a
    restart mid-day doesn't re-run a done job); (2) cross-process
    dedup — the per-channel agent processes (telegram + matrix) share
    the state dir, so whichever runs a job first records it and the
    other skips. No double journals.
  - The loop ticks every ~15 min and runs whatever's due. Jobs run in
    a thread (they may block / shell out) so they never stall the loop.
  - Never raises into the caller; a broken job is logged and skipped.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from windyfly.platform import windy_state_dir

logger = logging.getLogger(__name__)

TICK_SECONDS = 900  # 15 min

DueFn = Callable[["datetime | None", datetime], bool]
RunFn = Callable[[], Any]


@dataclass
class MaintenanceJob:
    name: str
    run: RunFn
    due: DueFn


# ── Due-checks (cadence helpers) ─────────────────────────────────────
def daily_due(after_hour: int = 0) -> DueFn:
    """Due once per calendar day (UTC), on/after ``after_hour``."""
    def _due(last: datetime | None, now: datetime) -> bool:
        if now.hour < after_hour:
            return False
        return last is None or last.date() < now.date()
    return _due


def weekly_due(weekday: int, after_hour: int = 0) -> DueFn:
    """Due once per week on ``weekday`` (Mon=0..Sun=6), on/after hour."""
    def _due(last: datetime | None, now: datetime) -> bool:
        if now.weekday() != weekday or now.hour < after_hour:
            return False
        if last is None:
            return True
        return (now.date() - last.date()).days >= 1
    return _due


def interval_due(seconds: float) -> DueFn:
    def _due(last: datetime | None, now: datetime) -> bool:
        return last is None or (now - last).total_seconds() >= seconds
    return _due


# ── Last-run persistence ─────────────────────────────────────────────
def _runs_path(state_dir: Path | None = None) -> Path:
    return (state_dir or windy_state_dir()) / "maintenance-runs.json"


def _load_runs(state_dir: Path | None = None) -> dict[str, str]:
    try:
        p = _runs_path(state_dir)
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}


def _save_run(name: str, when: datetime, state_dir: Path | None = None) -> None:
    try:
        p = _runs_path(state_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        runs = _load_runs(state_dir)
        runs[name] = when.isoformat()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(runs))
        import os
        os.replace(tmp, p)
    except Exception as e:
        logger.debug("maintenance last-run save failed: %s", e)


def _last_run(name: str, state_dir: Path | None = None) -> datetime | None:
    raw = _load_runs(state_dir).get(name)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def run_due_jobs(
    jobs: list[MaintenanceJob],
    *,
    now: datetime | None = None,
    state_dir: Path | None = None,
) -> list[str]:
    """Run every due job once; record its run. Returns names that ran."""
    now = now or datetime.now(timezone.utc)
    ran: list[str] = []
    for job in jobs:
        try:
            if job.due(_last_run(job.name, state_dir), now):
                job.run()
                _save_run(job.name, now, state_dir)
                ran.append(job.name)
                logger.info("maintenance job ran: %s", job.name)
        except Exception as e:  # noqa: BLE001 — one bad job never blocks others
            logger.warning("maintenance job %s failed (non-fatal): %s",
                           job.name, e)
    return ran


# ── Default job registry ─────────────────────────────────────────────
def default_jobs(config: dict[str, Any]) -> list[MaintenanceJob]:
    """The cross-platform maintenance jobs. Starts with the Journal (was
    a Linux-only daily systemd timer); the fire drill + continuity
    battery fold in as they're made OS-portable via the supervisor."""
    def _write_yesterday_journal() -> None:
        from datetime import timedelta
        from windyfly.memory.database import Database
        from windyfly.memory.journal import write_day
        db_path = (config.get("memory", {}) or {}).get("db_path", "data/windyfly.db")
        db = Database(db_path)
        day = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        model_caller = _journal_model_caller(config)
        write_day(db, day, model_caller=model_caller)

    return [
        MaintenanceJob(
            name="journal.daily",
            run=_write_yesterday_journal,
            due=daily_due(after_hour=0),
        ),
    ]


def _journal_model_caller(config: dict[str, Any]):
    try:
        from windyfly.agent.models import call_llm
    except Exception:
        return None

    def _call(messages, *, max_tokens=700):
        model = (config.get("agent", {}) or {}).get("journal_model", "claude-haiku-4-5")
        res = call_llm(messages, model=model, max_tokens=max_tokens,
                       temperature=0.3, config=config)
        return res.get("content", "") if isinstance(res, dict) else ""
    return _call


async def maintenance_loop(
    config: dict[str, Any],
    *,
    jobs: list[MaintenanceJob] | None = None,
    stop_event: asyncio.Event | None = None,
    tick_seconds: float = TICK_SECONDS,
) -> None:
    """Long-running in-process maintenance loop. Started by the channel
    adapter alongside goal-pacing. Never raises; jobs run in a thread."""
    stop_event = stop_event or asyncio.Event()
    jobs = jobs if jobs is not None else default_jobs(config)
    logger.info("maintenance scheduler started (%d jobs, tick=%ds)",
                len(jobs), int(tick_seconds))
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(run_due_jobs, jobs)
        except Exception as e:  # noqa: BLE001
            logger.warning("maintenance tick errored: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=tick_seconds)
        except asyncio.TimeoutError:
            continue
    logger.info("maintenance scheduler stopped")
