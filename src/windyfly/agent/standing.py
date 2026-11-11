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

What the probe never does is hammer Eternitas with a mint it knows will fail (Hub on #709). Two cases back off, doubling
from PROBE_S up to BACKOFF_MAX_S, and start again from PROBE_S the moment something changes:
- a key_revoked stop: a revoked key stays revoked, so its mint is retried ever more rarely; a NEW local key (after
  `windy home pair`) is tried at once;
- a passport-token agent that the public read calls active again but whose forced refresh is still refused.
A retired or suspended stop keeps the plain 60 s check, so a restore still takes effect within a minute.
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
    # What Eternitas really sends for a dead key is a 401 with key_status beside its usual code (agent-key-ops.v1);
    # eternitas.agent_keys.refusal_code reads that and hands this module "key_revoked".
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
# (Boss + Hub #9, 11-04). key_revoked + reason moved_away = LEGAL DECISION 58 (BOARD 2026-11-11T07:07:42Z), word for
# word: on the OLD computer after a move that worked, the person needs to know nothing is broken. Every other
# key_revoked (owner_revoked, self_revoked, ept_cutoff, passport_revoked, a retired key, other) keeps the neutral
# line, which makes no claim about why.
def answer(stop: dict[str, Any] | None = None, name: str | None = None, *, owner: bool = True) -> str:
    """The one plain line. Only the OWNER hears why and what they can do (a restore date, a command): anyone else
    who writes gets the same neutral line whatever the stop is (Hub's ruling on agents-retire 1.1, #294)."""
    stop = stop or stopped() or {}
    who = name or _agent_name()
    if not owner:
        return f"{who} isn't answering right now."
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
    if kind == "key_revoked" and stop.get("reason") == "moved_away":
        return f"{who} has moved to another computer, so it isn't answering here anymore."
    return f"{who} can't run on this computer right now, so it isn't answering here."


class AgentStopped(RuntimeError):
    """Eternitas refused this agent's token: never fall back to anything; say the standing line."""

    def __init__(self, stop: dict[str, Any] | None = None) -> None:
        self.stop = stop or stopped() or {}
        super().__init__(f"agent stopped by Eternitas ({self.stop.get('code', '?')})")


# ── the 60 s probe ───────────────────────────────────────────────────────────────────────────────────────────────────

_probe: dict[str, Any] = {"thread": None, "stop": None}

BACKOFF_MAX_S = 3600.0
# A REVOKED passport is final (agents-retire 1.1: the CRL keeps it), so a stop of that kind is checked once an hour,
# never every minute for ever (Hub on #294; his never-hammer rule on #709).
REVOKED_RECHECK_S = 3600.0
_revoked = {"next_at": 0.0}


class _Backoff:
    """The next time a known-failing attempt may run again: PROBE_S, then doubling up to BACKOFF_MAX_S. ``tag`` names
    what failed (a kid); a different tag starts afresh."""

    def __init__(self) -> None:
        self.n = 0
        self.next_at = 0.0
        self.tag: str | None = None

    def ready(self, tag: str | None = None, now: float | None = None) -> bool:
        if tag != self.tag:
            self.reset()
        return (time.time() if now is None else now) >= self.next_at

    def failed(self, tag: str | None = None, now: float | None = None) -> None:
        if tag != self.tag:
            self.reset()
        self.tag = tag
        self.n += 1
        self.next_at = (time.time() if now is None else now) + min(PROBE_S * 2 ** (self.n - 1), BACKOFF_MAX_S)

    def reset(self) -> None:
        self.n, self.next_at, self.tag = 0, 0.0, None


_key_backoff = _Backoff()
_refresh_backoff = _Backoff()


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

    held = stopped()
    if held and held.get("kind") == "revoked":
        t = time.time()
        if t < _revoked["next_at"]:
            raise _Refused("revoked_backoff")
        _revoked["next_at"] = t + REVOKED_RECHECK_S   # whatever this check answers, the next one waits an hour
    else:
        _revoked["next_at"] = 0.0

    if agent_keys.has_registered_key():
        stop = stopped()
        kid = _own_kid()
        if stop and stop.get("kind") == "key_revoked" and not _key_backoff.ready(kid):
            raise _Refused("key_revoked_backoff")                # this key was refused; try it again later
        try:
            out = agent_keys.request_agent_token(PROBE_AUD, min_ttl=10 ** 9)   # never the cached token
        except Exception as exc:
            if kind_of(getattr(exc, "code", "")) == "key_revoked":
                _key_backoff.failed(kid)
            raise
        _key_backoff.reset()
        return out

    passport = agent_keys.current_passport()   # ETERNITAS_PASSPORT, else the stored token's sub (as runtime_claim)
    if not passport:
        raise _Refused("no_passport")                    # a standalone install: nothing to check
    status = _verify_status(passport)
    code = _VERIFY_STOP.get(status or "")
    if code:
        if not stopped():
            latch(code)
        raise _Refused(code)
    if stopped() and status == "active":
        # Eternitas says active again (a restore): a forced refresh, the good mint that lifts the stop. Never a
        # refresh while it stays retired (a retire can last weeks), and a refused one backs off (Hub on #709).
        if not _refresh_backoff.ready():
            raise _Refused("refresh_backoff")
        from windyfly.eternitas import ept_refresh

        res = ept_refresh.refresh_ept(force=True)        # a usable token lifts the stop (ept_refresh), a refusal keeps it
        if res.get("status") == "failed" or stopped():
            _refresh_backoff.failed()
            raise _Refused(str(res.get("code") or "refused"))
        _refresh_backoff.reset()
        return res
    _refresh_backoff.reset()
    return status


def _own_kid() -> str | None:
    try:
        from windyfly.eternitas import agent_keys

        entry = agent_keys.active_entry(agent_keys.load_credentials())
        return str(entry.get("kid")) if entry else None
    except Exception:  # noqa: BLE001
        return None


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
