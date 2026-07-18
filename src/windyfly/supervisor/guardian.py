"""The Guardian — a tiny cross-platform wedge-catcher (Tier 3).

The one recovery case an in-process scheduler structurally CANNOT
handle: a fully-wedged agent (deadlocked event loop) can neither run
its own maintenance nor restart itself. The guardian is a separate,
deliberately-dumb process that watches the agent from OUTSIDE and
restarts it when it's dead or wedged.

Design rules (so the watchdog itself can never wedge):
  - NO LLM, NO database, NO async. A blocking sleep-loop with hard
    timeouts on every external call.
  - stdlib only.
  - Restart is delegated to an injected callable (Tier 1 OS backend
    wires the real one; tests inject a fake). The guardian decides
    WHEN to restart; the backend knows HOW on this OS.
  - Verified restart (the PR #294 lesson): a restart only resets the
    fail counter once the agent is actually healthy again. A failed
    restart keeps counting + keeps trying + keeps screaming.

Health = ALL configured checks must pass:
  1. heartbeat fresh  — heartbeat-<channel>.json newer than max_age
  2. process alive     — the pid in the heartbeat is running
  3. (optional) external liveness — a caller-supplied probe (e.g.
     Telegram getMe), so a token-revoked-but-running agent is caught.
Any check that can't run (e.g. no heartbeat file yet) is SKIPPED, not
failed — the guardian is conservative about killing a maybe-healthy
agent, but a present-and-stale signal IS a failure.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from windyfly.platform import process_alive, windy_state_dir
from windyfly.supervisor.heartbeat import heartbeat_age, read_heartbeat


@dataclass
class HealthResult:
    healthy: bool
    detail: str


@dataclass
class GuardianConfig:
    channels: list[str] = field(default_factory=lambda: ["telegram", "matrix"])
    interval_s: float = 60.0
    fail_limit: int = 3
    heartbeat_max_age_s: float = 900.0  # 3x the 5-min heartbeat
    # Optional external liveness probe: () -> (ok, detail). None = skip.
    external_probe: Callable[[], tuple[bool, str]] | None = None
    state_dir_override: str | None = None


def check_health(cfg: GuardianConfig, *, now: float | None = None) -> HealthResult:
    """Run all checks. Healthy iff every runnable check passes."""
    now = now if now is not None else time.time()
    state_dir = None
    if cfg.state_dir_override:
        from pathlib import Path
        state_dir = Path(cfg.state_dir_override)

    fails: list[str] = []
    any_channel_signal = False

    for ch in cfg.channels:
        hb = read_heartbeat(ch, state_dir)
        if hb is None:
            continue  # channel not reporting yet — skip, don't fail
        any_channel_signal = True
        age = heartbeat_age(ch, state_dir, now=now)
        if age is None or age > cfg.heartbeat_max_age_s:
            fails.append(f"{ch}:heartbeat_stale({age:.0f}s)")
            continue
        pid = hb.get("pid")
        if isinstance(pid, int) and not process_alive(pid):
            fails.append(f"{ch}:pid_{pid}_dead")
            continue
        if hb.get("polling") is False:
            fails.append(f"{ch}:polling_dead")

    # External probe (e.g. Telegram getMe) — catches token/network death
    if cfg.external_probe is not None:
        try:
            ok, detail = cfg.external_probe()
            if not ok:
                fails.append(f"external:{detail}")
        except Exception as e:  # noqa: BLE001 — a broken probe must not wedge
            fails.append(f"external:probe_error:{type(e).__name__}")

    if fails:
        return HealthResult(False, "; ".join(fails))
    if not any_channel_signal and cfg.external_probe is None:
        # Nothing to go on at all — treat as healthy (conservative;
        # don't restart an agent we can't observe).
        return HealthResult(True, "no signals yet (conservative pass)")
    return HealthResult(True, "all checks pass")


@dataclass
class GuardianState:
    consecutive_fails: int = 0
    last_result: str = "init"


def tick(
    cfg: GuardianConfig,
    state: GuardianState,
    restart_fn: Callable[[], bool],
    *,
    now: float | None = None,
) -> GuardianState:
    """One guardian cycle. Returns the updated state.

    Verified-restart discipline: fail_limit consecutive failures trigger
    restart_fn; the counter only resets to 0 when the agent is healthy
    again (a healthy tick, OR a restart that VERIFIES healthy). A restart
    that doesn't take keeps the counter climbing.
    """
    res = check_health(cfg, now=now)
    if res.healthy:
        state.consecutive_fails = 0
        state.last_result = f"OK: {res.detail}"
        return state

    state.consecutive_fails += 1
    state.last_result = f"FAIL({state.consecutive_fails}): {res.detail}"
    if state.consecutive_fails >= cfg.fail_limit:
        try:
            restarted = restart_fn()
        except Exception as e:  # noqa: BLE001
            restarted = False
            state.last_result += f" | restart_error:{type(e).__name__}"
        if restarted:
            # Do NOT blindly reset — verify on the NEXT tick. But record
            # that we acted so an operator sees it.
            state.last_result += " | RESTART_TRIGGERED"
            state.consecutive_fails = max(0, cfg.fail_limit - 1)
            # (parked one below the limit: one more failed tick re-fires,
            #  so a restart that didn't take keeps trying every cycle.)
        else:
            state.last_result += " | RESTART_FAILED"
    return state


def write_status(cfg: GuardianConfig, state: GuardianState) -> None:
    try:
        from pathlib import Path
        base = (
            Path(cfg.state_dir_override) if cfg.state_dir_override
            else windy_state_dir()
        )
        base.mkdir(parents=True, exist_ok=True)
        (base / "guardian.status").write_text(
            f"ts={time.time():.0f}\n"
            f"consecutive_fails={state.consecutive_fails}\n"
            f"detail={state.last_result}\n"
        )
    except Exception:
        pass


def run_forever(
    cfg: GuardianConfig, restart_fn: Callable[[], bool],
) -> None:  # pragma: no cover — the blocking loop; tick() is the tested unit
    state = GuardianState()
    while True:
        state = tick(cfg, state, restart_fn)
        write_status(cfg, state)
        time.sleep(cfg.interval_s)
