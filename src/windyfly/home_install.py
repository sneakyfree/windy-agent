"""`windy home install --ticket -`: Windy Word's desktop door (home handover, desktop pickup code: windy-contracts #273).

Windy Word holds the owner's fresh sign-in; this installer holds nothing of the owner's. The handshake:
1. This command makes the computer's agent key and prints ONE JSON line first: {"event": "kid", "kid": "<thumbprint>"}.
2. Word starts the handover with that kid (door "desktop") and writes "<handover_id>.<pickup_code>" + newline to OUR
   STDIN, and nowhere else (never argv, the environment, a file or a log: P8/P9). We wait up to STDIN_WAIT_S for it,
   showing the seconds so far; stdin closing (Word gone) ends the round at once.
3. We redeem: GET {hub}/api/v1/agent/handover/{id}/credentials with X-Windy-Pickup-Code and X-Windy-Pickup-Proof (a JWS
   by this computer's key: agent_keys.pickup_proof). 428 while the platforms ack (stage + waiting_for shown), 200 ONCE.
4. Then the same steps as every door (home_pair.settle_home): this computer's key is registered FIRST with the owner
   bootstrap token, then the memory once from /memory (the same two headers, a fresh proof), then the home record.

Nothing is ever printed or stored that holds the pickup code, the bootstrap token or a proof. The kid is not a secret.
"""

from __future__ import annotations

import queue
import re
import sys
import threading
import time
from typing import Any, Callable

import httpx
from rich.markup import escape

from windyfly.home_pair import Report

STDIN_WAIT_S = 15 * 60          # P10's sign-in again can take minutes, and Word keeps this same installer (kid)
HARD_CAP_S = 45 * 60 + 60       # the Hub ends a stuck round 45 min after its start (P2); ours is only a backstop
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
_FINAL = {401: "pickup_code_invalid", 403: "refused", 404: "handover_not_found", 410: "gone"}
_NOT_HERE_SAY = "  Windy couldn't bring your agent to this computer ({code}). Nothing on this computer was changed."
EOF_LINE = object()


class StdinLines:
    """The ticket line from stdin, read in a thread so the wait can show a moving number. ``get(timeout)`` returns the
    line, EOF_LINE when stdin closed, or None when nothing came within ``timeout``."""

    def __init__(self, stream: Any = None) -> None:
        self._q: queue.Queue[Any] = queue.Queue()
        stream = stream or sys.stdin

        def read() -> None:
            try:
                line = stream.readline()
            except Exception:  # noqa: BLE001
                line = ""
            self._q.put(line if line else EOF_LINE)

        threading.Thread(target=read, name="windyfly-ticket-stdin", daemon=True).start()

    def get(self, timeout: float) -> Any:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


def install(console: Any, *, ticket: str, json_status: bool = True, lines: Any = None,
            transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
            now: Callable[[], float] = time.time, db_factory: Callable[[], Any] | None = None, out: Any = None) -> int:
    """One desktop-door round. 0 = the agent lives here now; 1 = it didn't (said why); 2 = misuse; 130 = cancelled."""
    from windyfly import home_pair, hub_hatch
    from windyfly.eternitas import agent_keys

    report = Report(console, json_status, out)
    if ticket != "-":
        # Never echo what was passed: it may BE the code (P9: the code comes on stdin only).
        report("error", code="ticket_from_stdin_only",
               say="  The ticket is read from stdin only: run windy home install --ticket -")
        return 2
    rec = hub_hatch.cloud_agent()
    if rec.get("where") == "home":
        report("error", code="already_home", passport=rec.get("passport_number"),
               say=f"  {escape(str(rec.get('agent_name') or 'Your agent'))} already lives on this computer.")
        return 1
    home_pair._load_agent_env()
    pending = home_pair._pending()
    if pending and agent_keys.has_registered_key() and home_pair._key_passport() == pending.get("passport_number"):
        # A round that registered this computer's key and then stopped: FINISH it, as home pair does (Hub 11-05). No
        # new key, no kid line, no ticket read (one Word sends anyway is never used), and only the steps after the
        # registration: the pickup code is spent, so the memory is reported lost, never fetched with a guess.
        report("resumed", passport=pending.get("passport_number"))
        return home_pair._finish(pending, report=report, now=now, mem={"status": "lost"})
    home_pair._clear_pending()  # a marker without its registered key: that round never got that far
    if agent_keys.has_registered_key() or (agent_keys.current_passport() and
                                           agent_keys.current_passport() != rec.get("passport_number")):
        report("error", code="another_agent_here", say=home_pair._ANOTHER_SAY)
        return 1
    kid, undo = agent_keys.begin_local_key()
    report("kid", kid=kid)
    state: dict[str, Any] = {"keep_key": False, "cloud_record": rec}
    rc: int | None = None
    try:
        rc = _round(report, lines if lines is not None else StdinLines(), transport=transport, sleep=sleep, now=now,
                    db_factory=db_factory, state=state)
        return rc
    except KeyboardInterrupt:
        report("error", code="cancelled", say=home_pair._CANCEL_SAY)
        rc = 130
        return rc
    except Exception as exc:
        report("error", code="internal", detail=type(exc).__name__)
        if not json_status:
            raise
        rc = 1
        return rc
    finally:
        report.end_progress()
        if rc != 0 and not state["keep_key"] and not agent_keys.has_registered_key():
            undo()  # the key this round made goes again: nothing on this computer changed


def _wait_for_ticket(report: Report, lines: Any, now: Callable[[], float]) -> str | None:
    from windyfly.home_pair import TICK_S

    start = now()
    while True:
        got = lines.get(TICK_S)
        if got is EOF_LINE:
            report("error", code="stdin_closed", say="  Windy Word stopped before it sent the ticket. Nothing was "
                                                     "changed.")
            return None
        if got is not None:
            return str(got).strip()
        waited = int(now() - start)
        if now() - start >= STDIN_WAIT_S:
            report("error", code="stdin_timeout", say="  No ticket came from Windy Word. Nothing was changed.")
            return None
        report.progress("waiting_for_ticket", f"  Waiting for Windy Word ({waited // 60}:{waited % 60:02d}).",
                        seconds_in_stage=waited)


def _round(report: Report, lines: Any, *, transport: httpx.BaseTransport | None, sleep: Callable[[float], None],
           now: Callable[[], float], db_factory: Callable[[], Any] | None, state: dict[str, Any]) -> int:
    from windyfly import home_pair
    from windyfly.eternitas import agent_keys
    from windyfly.hub_login import hub_url

    line = _wait_for_ticket(report, lines, now)
    if line is None:
        return 1
    handover_id, _, code = line.rpartition(".")      # split on the LAST "." (the code itself has none)
    if not _ID_RE.match(handover_id) or not _CODE_RE.match(code):
        report("error", code="bad_ticket", say=_NOT_HERE_SAY.format(code="bad_ticket"))
        return 1
    base = f"{hub_url()}/api/v1/agent/handover/{handover_id}"

    def headers(url: str) -> dict[str, str]:
        return {"Accept": "application/json", "X-Windy-Pickup-Code": code,
                "X-Windy-Pickup-Proof": agent_keys.pickup_proof(handover_id, "GET", url)}

    body = _redeem(report, f"{base}/credentials", headers, transport=transport, sleep=sleep, now=now)
    if body is None:
        return 1
    ete: dict[str, Any] = body["eternitas"] if isinstance(body.get("eternitas"), dict) else {}
    pickup: dict[str, Any] = {
        "passport_number": ete.get("passport_number") or body.get("passport_number"),
        "bootstrap_token": ete.get("bootstrap_token") or body.get("bootstrap_token"),
        "agent_name": body.get("agent_name"),
    }
    if not ("memory" in body and body["memory"] is None):
        meta = dict(body["memory"]) if isinstance(body.get("memory"), dict) else {}
        meta["url"] = f"{base}/memory"
        pickup["memory"] = meta
    return home_pair.settle_home(pickup, report=report, door="desktop", transport=transport, db_factory=db_factory,
                                 now=now, state=state, memory_headers=headers)


def _redeem(report: Report, url: str, headers: Callable[[str], dict[str, str]], *,
            transport: httpx.BaseTransport | None, sleep: Callable[[float], None],
            now: Callable[[], float]) -> dict[str, Any] | None:
    """Poll the pickup: 428 while the platforms ack, 200 ONCE. Transient trouble (5xx, the network) is retried until
    the backstop; a 4xx is final (nothing was consumed: the Hub checks before it consumes)."""
    from windyfly import home_pair

    deadline = now() + HARD_CAP_S
    # The owner already approved in Windy Word: what's left is the platforms finishing (Chat, Mail).
    wait: dict[str, Any] = {"body": {"stage": "approved_finishing"}, "body_t": now(), "stage": None, "since": now()}

    def tick() -> None:
        home_pair._show_wait(report, wait, now())

    with httpx.Client(timeout=home_pair._HTTP_TIMEOUT, transport=transport) as client:
        first = True
        while True:
            if not first:
                home_pair._nap(home_pair.TICK_S, sleep, tick)
            first = False
            if now() > deadline:
                report("error", code="expired", say=_NOT_HERE_SAY.format(code="expired"))
                return None
            try:
                resp = client.get(url, headers=headers(url))
            except httpx.HTTPError:
                tick()
                continue
            if resp.status_code == 200:
                data = home_pair._json(resp)
                if data is None:
                    report("error", code="bad_response", say=_NOT_HERE_SAY.format(code="bad_response"))
                    return None
                report("approved")
                return data
            if resp.status_code == 428:
                got = home_pair._json(resp) or {}
                wait["body"] = {"stage": "approved_finishing", "waiting_for": got.get("waiting_for")}
                wait["body_t"] = now()
                tick()
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                tick()
                continue
            code = home_pair._error_code(resp) or _FINAL.get(resp.status_code) or f"http_{resp.status_code}"
            report("error", code=code, http=resp.status_code, say=_NOT_HERE_SAY.format(code=code))
            return None
