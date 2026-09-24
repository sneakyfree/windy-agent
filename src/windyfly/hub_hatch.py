"""`windy go` opens the ONE hub hatch ceremony (ADR-059; the default since 0.7.3).

There is one ceremony page, ``app.windyword.ai/hatch``. The terminal no
longer hatches by itself: it creates a *hatch ticket* (a pending ceremony,
nothing minted), prints the page link and a short code, and polls the
ticket while the person names and hatches their agent in the browser.
The passport is minted at "It's alive!" on the page, never here, so an
abandoned ceremony leaves nothing behind.

    POST {hub}/api/v1/agent/hatch/tickets        (owner Bearer, Idempotency-Key)
         {"source": "cli", "agent_name"?: "...", "then": "stay"}
      201/200 {ticket_id, ceremony_url, user_code, expires_at, poll_url, poll_interval_s}
      409 {"error": "owner_has_agent", passport_number, agent{name}}
    GET  {poll_url}                              → {status, result?, …}
    POST {hub}/api/v1/agent/hatch/tickets/{id}/cancel

The agent is born in the cloud and STAYS there (``then: "stay"``): no EPT
and no local body are written. This machine only remembers "your agent
(cloud)" so the next ``windy go`` says so; running it here is
``windy bring-home`` (the hub handover; see bring_home.py).

Spec: ~/windy-orchestra/specs/HATCH_CEREMONY_PAGE.md §1 + §4.
Tokens are never logged: only the ticket id, passport and status.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FLAG_ENV = "WINDY_HATCH_VIA_HUB"
TICKETS_PATH = "/api/v1/agent/hatch/tickets"
MIN_POLL_S = 2.0            # the hub rate-limits faster polling with 429
DEFAULT_POLL_S = 3.0
_HTTP_TIMEOUT = 30.0
TERMINAL = ("complete", "partial", "failed", "expired", "cancelled")


def opted_out() -> bool:
    """``WINDY_HATCH_VIA_HUB=0``: the old terminal hatch, for one more release."""
    return os.environ.get(FLAG_ENV, "").strip().lower() in ("0", "false", "no", "off")


def enabled() -> bool:
    """The ceremony is the default ``windy go`` (ADR-059, 0.7.3)."""
    return not opted_out()


DEPRECATION_NOTE = (
    "WINDY_HATCH_VIA_HUB=0: using the old terminal hatch — "
    "the old terminal hatch goes away in the next release."
)


# ── local state (beside the hub session, 0600) ───────────────────────

def _state_dir() -> Path:
    from windyfly.hub_login import session_path

    return session_path().parent


def install_key_path() -> Path:
    return _state_dir() / "hub_hatch_install.json"


def cloud_agent_path() -> Path:
    return _state_dir() / "cloud_agent.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(data).encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _install_state() -> dict[str, Any]:
    state = _read_json(install_key_path())
    if not isinstance(state.get("install_id"), str) or not state["install_id"]:
        state = {"install_id": secrets.token_hex(16), "attempt": 0}
        _write_private_json(install_key_path(), state)
    return state


def idempotency_key() -> str:
    """This install's ticket key: stable across runs, 1–128 chars.

    ``attempt`` moves on only after a ticket ended without an agent
    (failed / expired / cancelled), so a retry of the SAME ceremony reuses
    the pending ticket, and a fresh ``windy go`` after a dead one gets a
    new ticket instead of the dead one back.
    """
    state = _install_state()
    return f"windyfly-cli-{state['install_id']}-{int(state.get('attempt') or 0)}"


def next_attempt() -> None:
    state = _install_state()
    state["attempt"] = int(state.get("attempt") or 0) + 1
    _write_private_json(install_key_path(), state)


def cloud_agent() -> dict[str, Any]:
    """The owner's cloud agent this machine knows about, or {}."""
    rec = _read_json(cloud_agent_path())
    return rec if rec.get("passport_number") else {}


def remember_cloud_agent(result: dict[str, Any], *, ticket_id: str = "") -> dict[str, Any]:
    agent = _dict(result.get("agent"))
    platforms = _dict(result.get("platforms"))
    chat = _dict(platforms.get("chat"))
    rec = {
        "passport_number": str(result.get("passport_number") or ""),
        "agent_name": str(agent.get("name") or ""),
        "where": "cloud",
        "matrix_user_id": str(chat.get("matrix_user_id") or ""),
        "dm_room_id": str(chat.get("dm_room_id") or ""),
        "chat_url": chat_link(chat),
        "ticket_id": ticket_id,
        "recorded_at": int(time.time()),
    }
    _write_private_json(cloud_agent_path(), rec)
    return rec


# ── the hub calls ────────────────────────────────────────────────────

@dataclass
class Ticket:
    kind: str                       # created | owner_has_agent | email_unverified | failed
    http_status: int = 0
    ticket_id: str = ""
    ceremony_url: str = ""
    user_code: str = ""
    expires_at: str = ""
    poll_url: str = ""
    poll_interval_s: float = DEFAULT_POLL_S
    passport_number: str = ""       # owner_has_agent
    agent_name: str = ""            # owner_has_agent
    code: str = ""
    message: str = ""


@dataclass
class TicketResult:
    status: str                     # complete | partial | failed | expired | cancelled | timeout | interrupted
    result: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _base() -> str:
    from windyfly.hub_login import hub_url

    return hub_url()


def create_ticket(
    token: str, agent_name: str = "", *, transport: httpx.BaseTransport | None = None,
) -> Ticket:
    """Start a pending ceremony. Mints nothing. Never raises."""
    body: dict[str, Any] = {"source": "cli", "then": "stay", "return_hint": "terminal"}
    if agent_name.strip():
        body["agent_name"] = agent_name.strip()
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
            resp = client.post(f"{_base()}{TICKETS_PATH}", json=body,
                               headers={**_headers(token), "Idempotency-Key": idempotency_key()})
    except httpx.HTTPError as exc:
        logger.warning("Hatch ticket: the hub was unreachable (%s)", type(exc).__name__)
        return Ticket(kind="failed", code="unreachable",
                      message="Couldn't reach the Windy hub; check your connection and retry.")
    data = _json(resp)
    status = resp.status_code
    error = str(data.get("error") or "")
    if status in (200, 201) and data.get("ticket_id"):
        agent = _dict(data.get("agent"))
        ticket = Ticket(
            kind="created", http_status=status, ticket_id=str(data["ticket_id"]),
            ceremony_url=str(data.get("ceremony_url") or ""), user_code=str(data.get("user_code") or ""),
            expires_at=str(data.get("expires_at") or ""), poll_url=str(data.get("poll_url") or ""),
            poll_interval_s=_interval(data.get("poll_interval_s")),
            agent_name=str(agent.get("name") or ""),
        )
        logger.info("Hatch ticket %s (HTTP %s)", ticket.ticket_id, status)
        return ticket
    if status == 409 and error == "owner_has_agent":
        agent = _dict(data.get("agent"))
        return Ticket(kind="owner_has_agent", http_status=status,
                      passport_number=str(data.get("passport_number") or ""),
                      agent_name=str(agent.get("name") or ""))
    if status == 403 and error == "email_unverified":
        return Ticket(kind="email_unverified", http_status=status,
                      message="Verify your Windy account's email address, then run `windy go` again.")
    return Ticket(kind="failed", http_status=status, code=error or str(status),
                  message=str(data.get("message") or data.get("detail") or "")[:200])


def _interval(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_POLL_S
    return max(value, MIN_POLL_S)


def _deadline(expires_at: str, now: float) -> float:
    try:
        return datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return now + 15 * 60


def _retry_after(resp: httpx.Response, fallback: float) -> float:
    try:
        return max(float(resp.headers.get("Retry-After", "")), fallback)
    except ValueError:
        return fallback


def wait_for_ticket(
    ticket: Ticket,
    token: str,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    on_status: Callable[[str], None] | None = None,
) -> TicketResult:
    """Poll the ticket until it ends, it expires, or the person presses Ctrl-C.

    Ctrl-C cancels the ticket at the hub (nothing was minted, so nothing is
    left behind). Never polls faster than every 2 s; honours 429 Retry-After.
    """
    url = ticket.poll_url or f"{TICKETS_PATH}/{ticket.ticket_id}"
    if not url.startswith("http"):
        url = f"{_base()}{url}"
    deadline = _deadline(ticket.expires_at, now())
    last_status = ""
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
            wait = ticket.poll_interval_s
            while True:
                if now() >= deadline and last_status in ("", "pending"):
                    return TicketResult(status="expired", detail="the ceremony link expired")
                sleep(wait)
                wait = ticket.poll_interval_s
                try:
                    resp = client.get(url, headers=_headers(token))
                except httpx.HTTPError as exc:
                    logger.info("Hatch ticket %s: poll failed (%s)", ticket.ticket_id, type(exc).__name__)
                    continue
                if resp.status_code == 429:
                    wait = _retry_after(resp, ticket.poll_interval_s)
                    continue
                data = _json(resp)
                if resp.status_code != 200:
                    if resp.status_code in (401, 403, 404, 410):
                        return TicketResult(status="failed",
                                            detail=f"the hub answered HTTP {resp.status_code} "
                                                   f"{str(data.get('error') or '')}".strip())
                    continue
                status = str(data.get("status") or "")
                if status != last_status and on_status:
                    on_status(status)
                last_status = status
                if status in TERMINAL:
                    result = _dict(data.get("result"))
                    detail = ", ".join(
                        f"{k} {data.get(k) or result.get(k)}" for k in ("stage", "code", "error")
                        if data.get(k) or result.get(k)
                    )
                    logger.info("Hatch ticket %s: %s", ticket.ticket_id, status)
                    return TicketResult(status=status, result=result, detail=detail)
    except KeyboardInterrupt:
        cancel_ticket(ticket.ticket_id, token, transport=transport)
        return TicketResult(status="interrupted", detail="cancelled from the terminal")


def cancel_ticket(ticket_id: str, token: str, *, transport: httpx.BaseTransport | None = None) -> bool:
    try:
        with httpx.Client(timeout=10.0, transport=transport) as client:
            resp = client.post(f"{_base()}{TICKETS_PATH}/{ticket_id}/cancel", headers=_headers(token))
        return resp.status_code < 400
    except httpx.HTTPError:
        return False


# ── `windy go` (flag on) ─────────────────────────────────────────────

CHAT_APP = "https://app.windychat.ai"


def chat_link(chat: dict[str, Any]) -> str:
    """The hub's own chat link when it sends one, else a DM link built from the room."""
    url = str(chat.get("url") or "").strip()
    if url:
        return url
    room = str(chat.get("dm_room_id") or "").strip()
    if room:
        from urllib.parse import quote

        return f"{CHAT_APP}/?agent_room={quote(room, safe='')}"
    return ""


def _say_hi(rec: dict[str, Any]) -> str:
    link = rec.get("chat_url") or chat_link(rec)
    if link:
        return f"Say hi: {link}"
    handle = rec.get("matrix_user_id") or ""
    return f"Say hi at {handle} in Windy Chat" if handle else "Say hi in Windy Chat"


def go(
    console: Any,
    *,
    force: bool = False,
    agent_name: str = "",
    open_browser: Callable[[str], Any] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> int:
    """The whole CLI door: ticket → link + code → poll → "It's alive!". Returns an exit code."""
    from windyfly import hub_login

    known = cloud_agent()
    if known and not force:
        name = known.get("agent_name") or "your agent"
        if known.get("where") == "home":
            console.print(
                f"  [bold]{name}[/bold] ({known['passport_number']}) lives on this machine. "
                f"{_say_hi(known)}\n  Start it with [bold]windy start[/bold]."
            )
            return 0
        console.print(
            f"  You already have [bold]{name}[/bold] ({known['passport_number']}) in the cloud. "
            f"{_say_hi(known)}\n"
            "  To run it on this machine: [bold]windy bring-home[/bold]."
        )
        return 0

    token = hub_login.get_access_token()
    if not token:
        try:
            hub_login.login(open_browser=True, echo=console.print)
        except hub_login.LoginError as exc:
            console.print(f"  [yellow]Sign-in didn't finish:[/yellow] {exc}")
            return 1
        token = hub_login.get_access_token()
        if not token:
            console.print("  Run [bold]windy login[/bold], then [bold]windy go[/bold].")
            return 1

    ticket = create_ticket(token, agent_name, transport=transport)
    if ticket.kind == "owner_has_agent":
        return _offer_existing(console, ticket)
    if ticket.kind == "email_unverified":
        console.print(f"  [yellow]{ticket.message}[/yellow]")
        return 1
    if ticket.kind != "created":
        console.print(f"  [red]Couldn't start the hatch[/red] (HTTP {ticket.http_status or '-'}, "
                      f"{ticket.code or 'no code'}){': ' + ticket.message if ticket.message else ''}")
        return 1

    console.print(f"\n  Open this to hatch your agent: [bold]{ticket.ceremony_url}[/bold]  "
                  f"(code [bold]{ticket.user_code}[/bold])")
    console.print("  Waiting for the ceremony… (Ctrl-C to cancel)")
    try:
        (open_browser or _open_browser)(ticket.ceremony_url)
    except Exception:
        pass

    def on_status(status: str) -> None:
        if status == "in_ceremony":
            console.print("  The ceremony has started in your browser…")

    outcome = wait_for_ticket(ticket, token, transport=transport, sleep=sleep, now=now,
                              on_status=on_status)
    if outcome.status in ("complete", "partial"):
        rec = remember_cloud_agent(outcome.result, ticket_id=ticket.ticket_id)
        if not rec["passport_number"]:
            console.print("  [yellow]The hub said the ceremony finished but sent no passport.[/yellow]")
            return 1
        console.print(f"\n  [bold green]It's alive![/bold green] {_say_hi(rec)}")
        console.print(f"  {rec['agent_name'] or 'Your agent'} ({rec['passport_number']}) lives in the cloud.")
        if outcome.status == "partial":
            console.print("  [dim]Some services are still being set up; the hub finishes them.[/dim]")
        from windyfly.observability import disclosure

        disclosure.after_hatch(lambda line: console.print(f"  [dim]{line}[/dim]"))
        return 0

    next_attempt()
    messages = {
        "failed": "The hatch failed",
        "expired": "The ceremony link expired",
        "cancelled": "The ceremony was cancelled",
        "interrupted": "Cancelled",
    }
    msg = messages.get(outcome.status, f"The hatch ended ({outcome.status})")
    # Only a ceremony that never reached "It's alive!" is known to have left
    # nothing behind; a failed one may have got partway at the hub.
    nothing = "Nothing was created. " if outcome.status != "failed" else ""
    console.print(f"  [yellow]{msg}.[/yellow]{' (' + outcome.detail + ')' if outcome.detail else ''} "
                  f"{nothing}Run [bold]windy go[/bold] to try again.")
    return 1


def _offer_existing(console: Any, ticket: Ticket) -> int:
    """The owner already has an agent: offer to remember it here. Never mint."""
    name = ticket.agent_name or "an agent"
    console.print(f"  Your Windy account already has [bold]{name}[/bold] ({ticket.passport_number}).")
    accept = os.environ.get("WINDY_HATCH_ADOPT_EXISTING", "").strip().lower() in ("1", "true", "yes")
    if not accept and _interactive():
        from rich.prompt import Confirm

        from windyfly import prompts

        accept = prompts.ask(Confirm.ask, "  Use that agent from this machine?", default=False)
    if not accept:
        console.print("  Nothing changed. (Set WINDY_HATCH_ADOPT_EXISTING=1 to accept without a prompt.)")
        return 1
    remember_cloud_agent({"passport_number": ticket.passport_number, "agent": {"name": ticket.agent_name}})
    console.print(f"  OK: {name} lives in the cloud. To run it on this machine: "
                  "[bold]windy bring-home[/bold].")
    return 0


def _interactive() -> bool:
    if os.environ.get("WINDY_HATCH_NONINTERACTIVE", "").strip().lower() in ("1", "true", "yes"):
        return False
    try:
        import sys

        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _open_browser(url: str) -> None:
    import webbrowser

    webbrowser.open(url)
