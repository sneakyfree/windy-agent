"""`windy home pair`: bring your agent home to THIS computer with a code your phone approves (Windy Connect #105).

The computer never sees its owner's sign-in. It asks Windy Connect for a code bound to its own signing key, the owner
approves the code on their phone (or types it), and the poll answers ONCE with the agent's passport and a one-time
bootstrap token. The computer registers its own key at Eternitas with that token, then talks to every Windy service
with its key (EPT+agent + DPoP), and takes the handed-over memory once.

    POST {connect}/v1/home/code   {device_name, client, client_version, agent_kid}
         -> {device_code, user_code "1234-5678", qr_url, expires_in, interval}
    POST {connect}/v1/home/poll   {device_code, agent_kid}
         428 pending | 403 denied | 410 expired | 502 bootstrap_unavailable
         | 200 ONCE {passport_number, agent_name, bootstrap_token, bootstrap_expires_at, memory: {url, ...} | null}
    POST {connect}/v1/home/cancel {device_code, agent_kid} -> {ok: true}

Secrets (device_code, bootstrap_token, the memory URL) are never printed, logged or stored. ``--json-status`` prints
one JSON object per line for a program driving this (Windy Word's desktop install): never a secret in it either.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any, Callable

import httpx
from rich.markup import escape

logger = logging.getLogger(__name__)

CONNECT_URL_ENV = "WINDY_CONNECT_URL"
DEFAULT_CONNECT_URL = "https://api.windyconnect.com"
CLIENT = "windyfly"
_HTTP_TIMEOUT = 15.0
_POLL_ERRORS = {403: "denied", 410: "expired", 502: "bootstrap_unavailable"}
_EXPIRED_SAY = "  The code expired before it was approved. Run this again for a new code."
# Boss 11-03 13:10Z FINAL. A 502 can only come AFTER the owner approved (Hub), when the cloud agent already stood down.
# The computer has no pickup yet, so it doesn't know the agent's name: "your agent".
_NOWHERE_502_SAY = ("  Windy couldn't finish the move, so your agent isn't running anywhere right now. Run this again "
                    "for a new code to finish it.")
_ANOTHER_SAY = ("  An agent is already set up on this computer, so another one can't be moved here. To bring a "
                "different agent home, use another computer.")
_CANCEL_SAY = "  Cancelled. If you already approved on your phone, run this again to finish."
_OLD_DOOR_SAY = ("  A move of this agent with windy bring-home is partway done on this computer. To finish it, run: "
                 "[bold]windy bring-home[/bold]")
APPROVED_TTL_S = 900  # Connect keeps an APPROVED code alive this long (home.ts); it answers 428 until then
# Grant 11-03 (via Boss): every wait longer than a few seconds shows a number that moves. A wait shows one at least
# every TICK_S, however long Connect's poll interval or Retry-After is; a long stage past SLOW_S also says so.
TICK_S = 5.0
SLOW_S = 60
_SHOW_EVERY_S = 1.0  # the download and the import: at most one progress line a second
_STAGES = ("waiting_for_approval", "approved_finishing")


class _QueryValuesHidden(logging.Filter):
    """httpx logs every request URL, query included ("HTTP Request: GET https://…/memory?claim=…"). The memory link's
    query IS the secret (Windy Connect: never log it), so request URLs are logged with their query VALUES hidden."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 2 and isinstance(args[1], httpx.URL) and args[1].query:
            keys = "&".join(f"{k}=…" for k in dict(args[1].params))
            record.args = (args[0], str(args[1].copy_with(query=None)) + f"?{keys}", *args[2:])
        return True


def _hide_query_values() -> None:
    log = logging.getLogger("httpx")
    if not any(isinstance(f, _QueryValuesHidden) for f in log.filters):
        log.addFilter(_QueryValuesHidden())


def connect_url() -> str:
    return (os.environ.get(CONNECT_URL_ENV, "").strip() or DEFAULT_CONNECT_URL).rstrip("/")


class Report:
    """Where progress goes: plain lines for a person, or one JSON object per line (``--json-status``)."""

    def __init__(self, console: Any, json_status: bool, out: Any = None) -> None:
        self.console, self.json_status, self.out = console, json_status, out or sys.stdout
        self._status: Any = None
        self._shown: Any = None

    def __call__(self, event: str, say: str = "", **fields: Any) -> None:
        if self.json_status:
            self.out.write(json.dumps({"event": event, **fields}) + "\n")
            self.out.flush()
        elif say:
            self.end_progress()
            self.console.print(say)

    def progress(self, stage: str, say: str, *, slow: bool = False, **fields: Any) -> None:
        """A long wait's moving number. --json-status: one "progress" line each call (Windy Word draws the bar).
        A person at a terminal: one line rewritten in place. Output that isn't a terminal (a log): a line only when the
        stage changes, turns slow, or waits for a different set of services (Hub #9: a line names only what is pending
        then), so a log never fills with counters."""
        if self.json_status:
            self("progress", stage=stage, slow=slow, **fields)
            return
        if getattr(self.console, "is_terminal", False):
            if self._status is None:
                self._status = self.console.status(say)
                self._status.start()
            else:
                self._status.update(say)
        elif (stage, slow, tuple(fields.get("waiting_for") or ())) != self._shown:
            self.console.print(say)
        self._shown = (stage, slow, tuple(fields.get("waiting_for") or ()))

    def end_progress(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None
        self._shown = None


def _version() -> str:
    try:
        from windyfly import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        return "unknown"


def _error_code(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    return err if isinstance(err, str) else ""


def pair(console: Any, *, json_status: bool = False, device_name: str = "", yes: bool = False,
         transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
         now: Callable[[], float] = time.time, db_factory: Callable[[], Any] | None = None, out: Any = None,
         ask: Callable[[str], str] | None = None) -> int:
    """Run one pairing round. 0 = the agent lives here now; 1 = it didn't happen (said why); 130 = cancelled."""
    from windyfly import hub_hatch
    from windyfly.eternitas import agent_keys

    report = Report(console, json_status, out)
    rec = hub_hatch.cloud_agent()
    if rec.get("where") == "home":
        report("error", code="already_home", passport=rec.get("passport_number"),
               say=f"  {escape(str(rec.get('agent_name') or 'Your agent'))} already lives on this computer.")
        return 1
    if rec.get("passport_number") and _old_door_partway(str(rec["passport_number"])):
        # `windy bring-home` picked this agent up and stopped before it finished: only that door can finish it (its
        # sealed pickup), and a new code would start a second move of the same agent (Hub on #700).
        report("error", code="bring_home_unfinished", say=_OLD_DOOR_SAY)
        return 1
    _load_agent_env()
    pending = _pending()
    if pending and agent_keys.has_registered_key() and _key_passport() == pending.get("passport_number"):
        # A round that registered this computer's key and then stopped (a crash, Ctrl-C): finish it (Hub on #700).
        return _finish(pending, report=report, now=now, mem=pending.get("memory") or {"status": "lost"})
    _clear_pending()  # a marker without its registered key: that round never got that far
    if agent_keys.has_registered_key() or (agent_keys.current_passport() and
                                           agent_keys.current_passport() != rec.get("passport_number")):
        # This computer already holds an agent's key (the code binds to that key, so it can never carry another
        # agent) or another agent's passport: refused BEFORE a code, so no cloud agent stands down for it.
        report("error", code="another_agent_here", say=_ANOTHER_SAY)
        return 1
    try:
        if rec and not _confirm_cloud_link(rec, report, json_status=json_status, yes=yes, ask=ask):
            return 1
    except (KeyboardInterrupt, EOFError):
        report("error", code="cancelled", say="  Cancelled. Nothing was changed.")  # no key, no code yet (Hub on #700)
        return 130
    state: dict[str, Any] = {"keep_key": False, "cloud_record": rec}
    kid, undo = agent_keys.begin_local_key()
    rc: int | None = None
    try:
        rc = _round(console, report, kid, json_status=json_status, device_name=device_name, transport=transport,
                    sleep=sleep, now=now, db_factory=db_factory, state=state)
        return rc
    except KeyboardInterrupt:
        report("error", code="cancelled", say=_CANCEL_SAY)
        rc = 130
        return rc
    except Exception as exc:
        # A program driving --json-status always gets a final event (Hub on #700); a person keeps the traceback.
        report("error", code="internal", detail=type(exc).__name__)
        if not json_status:
            raise
        rc = 1
        return rc
    finally:
        report.end_progress()
        if rc != 0 and not state["keep_key"] and not agent_keys.has_registered_key():
            undo()  # "Nothing was changed" stays true: the key this round made goes again (Boss 11-03)


def _confirm_cloud_link(rec: dict[str, Any], report: Report, *, json_status: bool, yes: bool,
                        ask: Callable[[str], str] | None) -> bool:
    """A computer that talks to an agent in the cloud (`windy go`): asked once, BEFORE any code (Boss 13:43Z; Hub #9).
    The owner picks the agent on the phone, so whether it is the same agent is only known after the code."""
    name = str(rec.get("agent_name") or "")
    question = (f"  This computer is set up to talk to {escape(name)} in the cloud. If you bring a different agent home "
                f"here, that link is replaced; {escape(name)} keeps running in the cloud. Continue? [y/N] " if name else
                "  This computer is set up to talk to an agent in the cloud. If you bring a different agent home here, "
                "that link is replaced; that agent keeps running in the cloud. Continue? [y/N] ")
    if yes:
        return True
    if json_status or ask is None:
        report("error", code="cloud_link_confirm_needed", agent_name=name or None,
               say=question.rstrip() + " (Run this again with --yes to continue.)")
        return False
    if ask(question).strip().lower() in ("y", "yes"):
        return True
    report("error", code="cancelled", say="  Cancelled. Nothing was changed.")
    return False


def _load_agent_env() -> None:
    try:
        from dotenv import load_dotenv

        from windyfly.eternitas.ept_refresh import resolve_env_file

        env_file = resolve_env_file()
        if env_file is not None:
            load_dotenv(env_file, override=False)
    except Exception:  # noqa: BLE001
        pass


def _key_passport() -> str:
    """Whose registered key this computer holds (the entry records it; older entries: this agent's passport)."""
    from windyfly.eternitas import agent_keys

    entry = agent_keys.active_entry(agent_keys.load_credentials()) or {}
    return str(entry.get("passport") or agent_keys.current_passport()) if entry.get("registered_at") else ""


def _old_door_partway(passport: str) -> bool:
    """`windy bring-home` sealed a pickup for this agent and hasn't installed it yet (its resume file)."""
    try:
        from windyfly import bring_home

        return bring_home._seal_path(passport).exists()
    except Exception:  # noqa: BLE001
        return False


def _pending_path() -> Any:
    from windyfly.platform import windy_state_dir

    return windy_state_dir() / "home-pair-pending.json"


def _pending() -> dict[str, Any]:
    try:
        data = json.loads(_pending_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("passport_number") else {}
    except (OSError, ValueError):
        return {}


def _clear_pending() -> None:
    try:
        _pending_path().unlink(missing_ok=True)
    except OSError:
        pass


def _json(resp: httpx.Response) -> dict[str, Any] | None:
    try:
        data = resp.json()
    except ValueError:
        return None  # a captive portal or a proxy page, not Windy Connect
    return data if isinstance(data, dict) else None


def _round(console: Any, report: Report, kid: str, *, json_status: bool, device_name: str,
           transport: httpx.BaseTransport | None, sleep: Callable[[float], None], now: Callable[[], float],
           db_factory: Callable[[], Any] | None, state: dict[str, Any]) -> int:
    from windyfly.bring_home import runtime_label

    base = connect_url()
    body = {"device_name": device_name or runtime_label(), "client": CLIENT, "client_version": _version(),
            "agent_kid": kid}
    with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
        try:
            resp = client.post(f"{base}/v1/home/code", json=body)
        except httpx.HTTPError as exc:
            report("error", code="unreachable", detail=type(exc).__name__,
                   say="  [red]Windy Connect can't be reached right now.[/red] Try again in a minute.")
            return 1
        if resp.status_code not in (200, 201):
            code = _error_code(resp) or f"http_{resp.status_code}"
            report("error", code=code, say=f"  [red]Windy Connect didn't accept this pairing.[/red] Run this again "
                                          f"for a new code. (Reason: {code})")
            return 1
        data = _json(resp)
        if data is None or not data.get("device_code"):
            report("error", code="bad_response", say="  [red]Windy Connect didn't accept this pairing.[/red] Run this "
                                                   "again for a new code. (Reason: bad_response)")
            return 1
        device_code = str(data.get("device_code") or "")
        user_code = str(data.get("user_code") or "")
        expires_in = int(data.get("expires_in") or 300)
        interval = max(1.0, float(data.get("interval") or 5))
        report("code", user_code=user_code, qr_url=data.get("qr_url"), expires_in=expires_in,
               say=f"  On your phone, open Windy and approve this computer. If it asks for a code, type "
                   f"[bold]{escape(user_code)}[/bold].")
        # Connect decides when the code is over (its 410). Ours is only a safety cap: an APPROVED code lives
        # APPROVED_TTL_S more while the birth and the Chat + Mail acks finish, and still answers 428 (Hub on #700).
        deadline = now() + expires_in + APPROVED_TTL_S + 60
        # Until Connect's first answer, the code just issued is waiting for the phone, with expires_in to go.
        wait: dict[str, Any] = {"body": {"stage": "waiting_for_approval", "expires_in": expires_in},
                                "body_t": now(), "stage": None, "since": now()}
        try:
            pickup = _poll(client, base, device_code, kid, report, sleep, now, deadline, interval, wait)
        except KeyboardInterrupt:
            try:
                client.post(f"{base}/v1/home/cancel", json={"device_code": device_code, "agent_kid": kid})
            except httpx.HTTPError:
                pass
            report("error", code="cancelled", say=_CANCEL_SAY)
            return 130
    if pickup is None:
        return 1
    return settle_home(pickup, report=report, transport=transport, db_factory=db_factory, door="connect", now=now,
                       state=state)


def _mmss(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def _show_wait(report: Report, wait: dict[str, Any], t: float) -> None:
    """Connect's 428 says where the round is (Connect #106): stage waiting_for_approval | approved_finishing, the
    seconds left (expires_in) and, while finishing, which services it waits for. An older Connect says none of it:
    then "waiting" with the seconds so far. Counted down (or up) locally between answers."""
    body = wait["body"]
    stage = body.get("stage") if body.get("stage") in _STAGES else "waiting"
    if stage != wait["stage"]:
        wait["stage"], wait["since"] = stage, t
    exp = body.get("expires_in")
    left = (max(0, int(exp - (t - wait["body_t"])))
            if isinstance(exp, (int, float)) and not isinstance(exp, bool) else None)
    in_stage = int(t - wait["since"])
    raw_for = body.get("waiting_for")
    waiting_for = [w for w in raw_for if w in ("chat", "mail")] if isinstance(raw_for, list) else []
    slow = stage != "waiting_for_approval" and in_stage >= SLOW_S
    fields: dict[str, Any] = {"seconds_left": left, "seconds_in_stage": in_stage}
    if waiting_for:
        fields["waiting_for"] = waiting_for
    time_left = f" ({_mmss(left)} left)" if left is not None else ""
    if stage == "waiting_for_approval":
        say = f"  Waiting for you to approve on your phone{time_left}."
    elif stage == "approved_finishing":
        names = " and ".join({"chat": "Windy Chat", "mail": "Windy Mail"}[w] for w in waiting_for)
        say = (f"  Approved. Finishing the move{time_left}." + (f" Waiting for {names}." if names else "")
               + (" Still finishing, this can take a few minutes." if slow else ""))
    else:
        say = (f"  Waiting for Windy ({_mmss(in_stage)})."
               + (" Still waiting, this can take a few minutes." if slow else ""))
    report.progress(stage, say, slow=slow, **fields)


def _nap(seconds: float, sleep: Callable[[float], None], tick: Callable[[], None]) -> None:
    """Sleep in steps of at most TICK_S, showing the wait between steps: a long interval never freezes the number."""
    left = seconds
    while left > 0:
        step = min(TICK_S, left)
        sleep(step)
        left -= step
        if left > 0:
            tick()


def _poll(client: httpx.Client, base: str, device_code: str, kid: str, report: Report,
          sleep: Callable[[float], None], now: Callable[[], float], deadline: float,
          interval: float, wait: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if wait is None:
        wait = {"body": {}, "body_t": now(), "stage": None, "since": now()}

    def tick() -> None:
        _show_wait(report, wait, now())

    while True:
        _nap(interval, sleep, tick)
        if now() > deadline:
            report("error", code="expired", say=_EXPIRED_SAY)
            return None
        try:
            resp = client.post(f"{base}/v1/home/poll", json={"device_code": device_code, "agent_kid": kid})
        except httpx.HTTPError:
            tick()
            continue  # a blip: the code is still good until the deadline
        if resp.status_code == 428:
            wait["body"], wait["body_t"] = _json(resp) or {}, now()
            tick()
            continue
        if resp.status_code == 429:
            tick()
            try:
                _nap(max(0.0, min(float(resp.headers.get("Retry-After") or interval), 60.0)), sleep, tick)
            except ValueError:
                pass
            continue
        if resp.status_code == 200:
            data = _json(resp)
            if data is not None:
                report("approved")
                return data
            code = "bad_response"
            report("error", code=code, say=f"  [red]Windy Connect didn't accept this pairing.[/red] Run this again "
                                           f"for a new code. (Reason: {code})")
            return None
        code = _POLL_ERRORS.get(resp.status_code) or _error_code(resp) or f"http_{resp.status_code}"
        say = {"denied": "  The pairing was declined on your phone. Nothing was changed.",
               "expired": _EXPIRED_SAY,
               "bootstrap_unavailable": _NOWHERE_502_SAY}
        report("error", code=code, say=say.get(code, f"  [red]Windy Connect didn't accept this pairing.[/red] Run "
                                                     f"this again for a new code. (Reason: {code})"))
        return None


def settle_home(pickup: dict[str, Any], *, report: Report, door: str,
                transport: httpx.BaseTransport | None = None, db_factory: Callable[[], Any] | None = None,
                now: Callable[[], float] = time.time, state: dict[str, Any] | None = None) -> int:
    """After a pickup that carries a bootstrap token (any door): register this computer's key FIRST (the step that
    can end the round), then the brain + settings, then the memory once, then the "it lives here" record.

    A pending marker is written BEFORE the registration, so a round that stops after it (a crash, Ctrl-C) is finished
    by the next run instead of leaving the agent running nowhere (Hub on #700)."""
    from windyfly import bring_home
    from windyfly.eternitas import agent_keys

    state = state if state is not None else {}
    _hide_query_values()  # before the memory link is fetched: its query is the secret
    passport = str(pickup.get("passport_number") or "")
    bootstrap = str(pickup.get("bootstrap_token") or "")
    name = str(pickup.get("agent_name") or "")
    who = escape(name or "Your agent")  # the agent's name only: never a passport or a machine name (Boss 11-03)
    nowhere = (f"  Windy couldn't finish the move, so {who if name else 'your agent'} isn't running anywhere right "
               "now. Run this again for a new code to finish it.")
    if not passport or not bootstrap:
        # The owner approved and the cloud agent stood down: line 4, never "nothing was changed" (Hub on #700).
        report("error", code="bad_pickup", say=nowhere)
        return 1
    from windyfly.state_files import write_private

    marker: dict[str, Any] = {"passport_number": passport, "agent_name": name, "door": door,
                              "cloud_record": state.get("cloud_record") or {}}
    write_private(_pending_path(), json.dumps(marker).encode())
    # From here a Ctrl-C (or a crash) may land after Eternitas took the key: the key stays, and the marker lets the
    # next run finish the move with it. Only a registration that surely failed gives the key back (Hub on #700).
    state["keep_key"] = True
    reg = agent_keys.register_with_bootstrap(passport, bootstrap, transport=transport)
    if reg.get("status") == "unknown":
        # Eternitas may hold the key already: keep it, so the next round's 409 key_exists settles it (Hub on #700).
        state["keep_key"] = True
        report("error", code="key_registration_unknown", detail=reg.get("code"), say=nowhere)
        return 1
    if reg.get("status") not in ("registered", "already_registered"):
        _clear_pending()
        state["keep_key"] = False
        if reg.get("code") in ("ConnectError", "ConnectTimeout"):
            # Eternitas was never reached: not a refusal of the key, so the plain running-nowhere line (Hub on #700).
            report("error", code="key_registration_unreachable", detail=reg.get("code"), say=nowhere)
            return 1
        report("error", code="key_registration_failed", http=reg.get("http"), detail=reg.get("code"),
               say=f"  [red]Eternitas didn't accept this computer's key[/red], so {who} couldn't move here, and it "
                   "isn't running anywhere right now. Run this again for a new code to finish the move.")
        return 1
    report("registered", passport=passport)
    mem: dict[str, Any] = {"status": "none"}
    memory = pickup.get("memory")
    if isinstance(memory, dict) and memory.get("url"):
        kw: dict[str, Any] = {"transport": transport, "progress": _memory_progress(report, who, now)}
        if db_factory is not None:
            kw["db_factory"] = db_factory
        try:
            mem = bring_home.install_memory(memory, passport, f"pair-{int(now())}", "", **kw)
        except httpx.HTTPError as exc:
            mem = {"status": "download_failed", "detail": type(exc).__name__}
        except Exception as exc:  # noqa: BLE001 — a broken import never strands the agent half-moved (Hub on #700)
            logger.warning("home pair: the memory import failed (%s)", type(exc).__name__)
            mem = {"status": "import_failed", "detail": type(exc).__name__}
    # The memory's outcome goes into the marker, so a stop between here and the end is resumed with the truth (not
    # "lost"); and _finish gets the marker from memory, never a re-read that could come back empty (Hub on #700).
    marker["memory"] = {k: mem[k] for k in ("status", "imported", "verified", "db_path") if k in mem}
    write_private(_pending_path(), json.dumps(marker).encode())
    return _finish(marker, report=report, now=now, mem=mem)


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def _memory_progress(report: Report, who: str, now: Callable[[], float]) -> Callable[[str, int, int | None], None]:
    """The memory's download (bytes; the total when Connect sends Content-Length) and its import (events), at most one
    line a second plus the last one."""
    last: dict[str, float] = {}

    def show(stage: str, done: int, total: int | None) -> None:
        t = now()
        finished = total is not None and done >= total
        if not finished and t - last.get(stage, float("-inf")) < _SHOW_EVERY_S:
            return
        last[stage] = t
        pct = min(100, done * 100 // total) if total else None
        if stage == "download":
            so_far = f"{_mb(done)} of {_mb(total)} ({pct}%)" if total else f"{_mb(done)} so far"
            report.progress("download", f"  Bringing over {who}'s memory: {so_far}.", bytes=done, total=total,
                            percent=pct)
        else:
            report.progress("import", f"  Saving {who}'s memory on this computer: {pct if pct is not None else 100}%"
                                      " done.", done=done, total=total, percent=pct)

    return show


def _finish(pending: dict[str, Any], *, report: Report, now: Callable[[], float], mem: dict[str, Any]) -> int:
    """The steps after the key is registered: the brain + settings, the record, the last words. Also the resume of a
    round that stopped after its registration (its memory link was spent then: line 8)."""
    import shutil

    from windyfly import bring_home, hub_hatch

    passport = str(pending.get("passport_number") or "")
    name = str(pending.get("agent_name") or "")
    who = escape(name or "Your agent")
    bring_home.ensure_brain(name)  # before the settings below: writing the brain config rewrites the env file
    bring_home.upsert_env({"ETERNITAS_PASSPORT": passport, "WINDY_CHAT_EPT_AGENT": "1", "WINDY_MAIL_EPT_AGENT": "1"})
    report("memory", status=mem.get("status"), imported=mem.get("imported", 0), verified=bool(mem.get("verified")),
           db_path=mem.get("db_path"),
           say=(f"  Memory: {mem.get('imported', 0)} messages carried over"
                + (f", saved in {escape(str(mem['db_path']))}." if mem.get("db_path") else ".")
                if mem.get("status") == "imported"
                else f"  [yellow]{who}'s memory couldn't be brought over[/yellow], so it starts here without it."
                if mem.get("status") not in ("none",) else ""))
    path = hub_hatch.cloud_agent_path()
    if pending.get("cloud_record") and path.exists():
        # The link to the agent in the cloud is replaced: its record is kept as a dated backup, never deleted (Boss).
        shutil.copy2(path, path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"))
    hub_hatch._write_private_json(path, {
        "passport_number": passport, "agent_name": name, "where": "home", "home_since": int(now()),
        "door": str(pending.get("door") or "")})
    _clear_pending()
    report("done", passport=passport, agent_name=name or None,
           say=f"\n  [bold green]{who} now lives on this computer.[/bold green] To start it in Windy Chat, run: "
               "[bold]windy start --channel matrix[/bold]")
    return 0
