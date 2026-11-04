"""The agent's standing at Eternitas: a refused mint STOPS it, like the owner's pause (Boss 11-04, Hub's #265 Q4).

When Eternitas refuses to mint this agent's token because the agent is retired, suspended or revoked, or because THIS
computer's key was revoked (a move that didn't finish: home handover 1.1 H13a), the agent stops:
- no model call of any kind (Windy Mind, a provider, or the local lifeboat);
- no tool, and nothing sent outward;
- the person who writes gets ONE plain line; another agent gets nothing (no sibling loop).

The stop is written to a file in the state folder, so a restart doesn't undo it. Only the next GOOD mint clears it: the
60 s probe (``start_probe``) tries one while the agent runs, so a restore takes effect within a minute.

Quiet within N seconds of the refusal: N = PROBE_S + one mint round trip (at most MINT_TIMEOUT_S) = 75 s worst case,
when the agent is idle; a turn that mints in between stops it at once.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

PROBE_S = 60.0
MINT_TIMEOUT_S = 15.0
QUIET_WITHIN_S = int(PROBE_S + MINT_TIMEOUT_S)

# Eternitas' mint refusals that mean STOP, and what each one is (the kind picks the person's line). A code not listed
# here is never a stop: a busy Eternitas, a bad proof or a not-yet-born passport keep their own handling.
STOP_CODES = {
    "agent_retired": "retired",
    "passport_retired": "retired",
    "passport_suspended": "suspended",
    "agent_suspended": "suspended",
    "passport_revoked": "revoked",
    "agent_revoked": "revoked",
    "agent_key_revoked": "key_revoked",
    "key_revoked": "key_revoked",
}

_lock = threading.Lock()
_cache: dict[str, Any] = {"mtime": None, "value": None}


def _path() -> Path:
    from windyfly.platform import windy_state_dir

    return windy_state_dir() / "standing-stop.json"


def kind_of(code: str | None) -> str | None:
    return STOP_CODES.get(str(code or "").strip().lower())


def latch(code: str, *, retired_until: Any = None, reason: Any = None) -> bool:
    """Record a stop for Eternitas' refusal ``code``. False (nothing written) when the code isn't a stop."""
    kind = kind_of(code)
    if kind is None:
        return False
    from windyfly.state_files import write_private

    rec: dict[str, Any] = {"code": str(code).strip().lower(), "kind": kind, "since": int(time.time())}
    if isinstance(retired_until, str) and _iso_date(retired_until):
        rec["retired_until"] = retired_until
    if isinstance(reason, str) and reason.strip():
        rec["reason"] = reason.strip().lower()[:40]
    with _lock:
        before = _read()
        write_private(_path(), json.dumps(rec).encode())
        _cache.update(mtime=None, value=None)
    if before is None:
        logger.warning("Eternitas refused this agent's token (%s): the agent is STOPPED until a good mint", rec["code"])
    return True


def clear() -> None:
    """A good mint: the stop (if any) ends."""
    with _lock:
        try:
            _path().unlink()
            logger.warning("Eternitas minted this agent's token again: the stop is lifted")
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("standing: could not lift the stop (%s)", type(exc).__name__)
        _cache.update(mtime=None, value=None)


def _read() -> dict[str, Any] | None:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("kind") else None


def stopped() -> dict[str, Any] | None:
    """The stop in force, or None. Cheap: re-read only when the file changes."""
    try:
        mtime = _path().stat().st_mtime_ns
    except OSError:
        return None
    with _lock:
        if _cache["mtime"] != mtime:
            _cache.update(mtime=mtime, value=_read())
        value = _cache["value"]
    return dict(value) if value else None


def _iso_date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def _agent_name() -> str:
    try:
        from windyfly import hub_hatch

        name = str(hub_hatch.cloud_agent().get("agent_name") or "").strip()
    except Exception:  # noqa: BLE001
        name = ""
    return name or "Your agent"


# The person's line for each kind. Retired = Boss's line (11-04, Hub #9 with #265 1.1); the date is Eternitas' own
# retired_until, never a guess: absent = the line without it. key_revoked + reason move_aborted = home 1.1 H13a's line
# (Boss + Hub #9, 11-04). Any other key_revoked stays neutral until Eternitas names the reason (Hub 11-04).
def answer(stop: dict[str, Any] | None = None, name: str | None = None) -> str:
    stop = stop or stopped() or {}
    who = name or _agent_name()
    kind = stop.get("kind")
    if kind == "retired":
        until = _iso_date(str(stop.get("retired_until") or ""))
        if until is not None:
            return (f"{who} is retired, so it isn't answering. You can restore it until "
                    f"{until.strftime('%B')} {until.day}, {until.year}. After that it stays closed, and its history "
                    "is kept.")
        # never an open-ended "you can restore it": only inside the undo window (Hub #9 on #709; #265 1.1: 30 days)
        return (f"{who} is retired, so it isn't answering. You can restore it for 30 days after it was retired, and "
                "its history is kept.")
    if kind == "suspended":
        return f"{who} is suspended, so it isn't answering right now."
    if kind == "revoked":
        return f"{who} was closed, so it isn't answering."
    if kind == "key_revoked" and stop.get("reason") == "move_aborted":
        return (f"The move to this computer didn't finish, so {who} is still running where it was, not on this "
                "computer. To try again, run windy home pair for a new code.")
    return f"{who} can't run on this computer right now, so it isn't answering here."


class AgentStopped(RuntimeError):
    """Eternitas refused this agent's token: never fall back to anything; say the standing line."""

    def __init__(self, stop: dict[str, Any] | None = None) -> None:
        self.stop = stop or stopped() or {}
        super().__init__(f"agent stopped by Eternitas ({self.stop.get('code', '?')})")


# ── the 60 s probe ───────────────────────────────────────────────────────────────────────────────────────────────────

_probe: dict[str, Any] = {"thread": None, "stop": None}


def probe_once(mint: Callable[[], Any] | None = None) -> str:
    """Try one mint now. "ok" (a stop, if any, is lifted), "stopped" (a refusal latched a stop), or "skipped" (no way
    to mint here, or Eternitas didn't answer: nothing changes)."""
    try:
        (mint or _mint)()
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", "")
        if kind_of(code):
            return "stopped"          # the mint path latched it (agent_keys / ept_refresh)
        return "skipped"
    return "ok"


def _mint() -> Any:
    """One check with Eternitas. A computer with its own key: a FRESH mode-B mint (a refusal latches, a token lifts).
    A passport-token agent: the public verify read every time (no mint needed to SEE a retire), and a forced refresh
    only while stopped, because only a good mint lifts the stop."""
    from windyfly.eternitas import agent_keys

    if agent_keys.has_registered_key():
        return agent_keys.request_agent_token(PROBE_AUD, min_ttl=10 ** 9)   # never the cached token
    import os

    passport = os.environ.get("ETERNITAS_PASSPORT", "").strip()
    if not passport:
        raise _Refused("no_passport")                    # a standalone install: nothing to check
    status = _verify_status(passport)
    code = _VERIFY_STOP.get(status or "")
    if code:
        if not stopped():
            latch(code)
        raise _Refused(code)
    if stopped() and status == "active":
        # Eternitas says active again (a restore): ONE forced refresh, the good mint that lifts the stop. Never a
        # refresh a minute while it stays retired (a retire can last weeks).
        from windyfly.eternitas import ept_refresh

        res = ept_refresh.refresh_ept(force=True)        # a 200 lifts the stop (ept_refresh), a refusal keeps it
        if res.get("status") == "failed":
            raise _Refused(str(res.get("code") or "refused"))
        return res
    return status


# The public registry read's statuses that mean STOP, as the mint refusal codes they stand for.
_VERIFY_STOP = {"retired": "agent_retired", "suspended": "passport_suspended", "revoked": "passport_revoked"}


def _verify_status(passport: str) -> str | None:
    import httpx

    from windyfly.eternitas.url import resolve_eternitas_url

    url = f"{resolve_eternitas_url('https://api.eternitas.ai').rstrip('/')}/api/v1/registry/verify/{passport}"
    try:
        r = httpx.get(url, timeout=MINT_TIMEOUT_S)
        data = r.json() if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001 — unreachable = no news, never a stop
        return None
    status = data.get("status") if isinstance(data, dict) else None
    return status if isinstance(status, str) else None


PROBE_AUD = "windy-mind"


class _Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def start_probe(interval: float = PROBE_S) -> None:
    """While the agent runs: one mint every ``interval`` seconds, in a daemon thread. Idempotent."""
    t = _probe.get("thread")
    if t is not None and t.is_alive():
        return
    stop = threading.Event()

    def run() -> None:
        while not stop.wait(interval):
            try:
                probe_once()
            except Exception as exc:  # noqa: BLE001 — the probe never dies on an error
                logger.debug("standing probe: %s", type(exc).__name__)

    _probe.update(stop=stop, thread=threading.Thread(target=run, name="windyfly-standing-probe", daemon=True))
    _probe["thread"].start()


def stop_probe() -> None:
    ev = _probe.get("stop")
    if ev is not None:
        ev.set()
    _probe.update(thread=None, stop=None)
