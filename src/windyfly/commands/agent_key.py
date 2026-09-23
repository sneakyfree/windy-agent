"""``windy agent-key`` — the agent's Eternitas signing key (agent-keys v1).

Subcommands::

    windy agent-key status           # local keys + what Eternitas publishes
    windy agent-key rotate           # new key, retire the old one
    windy agent-key reset            # OWNER recovery: fresh sign-in, new key, revoke the rest
    windy agent-key revoke --kid K   # revoke one key

Not ``windy keys``: that name already belongs to the wk_ bot credential.
Key material is never printed; only kids (public thumbprints).
See docs/AGENT_KEYS.md.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Callable

from rich.console import Console

console = Console()


def add_parser(sub: Any) -> None:
    p = sub.add_parser("agent-key", help="Manage the agent's Eternitas signing key")
    s = p.add_subparsers(dest="action", help="agent-key action")
    s.add_parser("status", help="Show the signing key(s) and what Eternitas publishes")
    s.add_parser("rotate", help="Register a new key and retire the old one")
    r = s.add_parser("reset", help="Owner recovery: fresh sign-in, new key, revoke the old ones")
    r.add_argument("--yes", "-y", action="store_true", help="Don't ask for confirmation")
    r.add_argument("--no-browser", action="store_true", help="Print the sign-in link instead of opening it")
    r.add_argument("--reason", choices=("recovery", "handover"), default="recovery",
                   help="recovery (lost/compromised key, default) or handover (moving the agent)")
    v = s.add_parser("revoke", help="Revoke one key by kid")
    v.add_argument("--kid", required=True, help="The key's kid (see `windy agent-key status`)")
    v.add_argument("--reason", default="revoked by owner", help="Reason recorded at Eternitas")
    v.add_argument("--yes", "-y", action="store_true", help="Don't ask for confirmation")


def _confirm(question: str, ask: Callable[[str], str]) -> bool:
    try:
        return ask(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


_MESSAGES = {
    "unsupported": "[yellow]This Eternitas doesn't support agent keys yet.[/yellow] Nothing changed.",
    "no_passport": "[yellow]This agent has no passport yet (not hatched?).[/yellow]",
    "needs_login": ("[yellow]No usable credential.[/yellow] Run [bold]windy login[/bold] then "
                    "[bold]windy ept refresh[/bold], or use [bold]windy agent-key reset[/bold] as the owner."),
    "rate_limited": ("[red]Refused: Eternitas allows 2 owner resets per passport per 24h.[/red] "
                     "Try again tomorrow."),
    "stale_auth": ("[red]Eternitas wants a sign-in from the last 10 minutes and didn't get one.[/red] "
                   "Run the reset again and sign in with your password when the browser asks."),
}


def _report(result: dict[str, Any]) -> int:
    st = result.get("status")
    if st in _MESSAGES:
        console.print(_MESSAGES[st])
        return 1
    if st == "failed":
        if "http" in result:
            extra = f"HTTP {result['http']} {result.get('code') or ''} {result.get('detail', '')}"
        else:
            extra = result.get("error", "")
        console.print(f"[red]Failed:[/red] {' '.join(extra.split())}".rstrip())
        if result.get("hint"):
            console.print(result["hint"])
        return 1
    return 0


def cmd_agent_key(args: argparse.Namespace, *, ask: Callable[[str], str] = input) -> int:
    from windyfly.eternitas import agent_keys as ak

    action = getattr(args, "action", None) or "status"
    if action == "status":
        info = ak.status()
        console.print(f"Passport: {info['passport'] or '—'}")
        console.print(f"Credentials file: {ak.credentials_path()}")
        remote = info["remote"]
        if not info["keys"]:
            console.print("No signing key yet (one is made on the next boot or `windy login`).")
        for k in info["keys"]:
            mark = "★" if k["kid"] == info["active_kid"] else " "
            there = remote.get(k["kid"], "not listed") if isinstance(remote, dict) else "?"
            console.print(f" {mark} {k['kid']}  {k['status']:<8}  created {k['created_at']}  "
                          f"registered {k['registered_at'] or 'no'}  eternitas: {there}")
        if not isinstance(remote, dict):
            console.print(f"Eternitas: {remote or 'not checked'}")
        return 0

    if action == "rotate":
        res = ak.rotate()
        if res.get("status") == "rotated":
            console.print(f"[green]✓ Rotated[/green] → {res['kid']} (old {res['old_kid'][:12]}… retiring)")
        return _report(res)

    if action == "reset":
        if not args.yes and not _confirm(
            "Reset REVOKES this agent's current signing key(s) and registers a new one. "
            "You'll sign in again in the browser; Eternitas emails you and allows 2 per 24h. Continue?",
            ask,
        ):
            console.print("Cancelled.")
            return 1
        no_browser = bool(getattr(args, "no_browser", False))
        def sign_in() -> str:
            return ak.fresh_owner_token(open_browser=not no_browser, echo=console.print)

        res = ak.reset(owner_token=sign_in, reason=getattr(args, "reason", "recovery"))
        if res.get("status") == "stale_auth":
            # Eternitas saw no fresh credential entry (e.g. the hub kept an old
            # auth_time); one more prompt=login round is the documented remedy.
            console.print("[yellow]That sign-in wasn't fresh enough; opening the browser once more.[/yellow]")
            res = ak.reset(owner_token=sign_in, reason=getattr(args, "reason", "recovery"))
        if res.get("status") == "reset":
            console.print(f"[green]✓ New signing key[/green] {res['kid']} (revoked {len(res['revoked'])} old)")
            if res.get("revoke_failed"):
                console.print(f"[yellow]Could not revoke:[/yellow] {', '.join(res['revoke_failed'])}")
        return _report(res)

    if action == "revoke":
        if not args.yes and not _confirm(
            f"Revoke key {args.kid}? Everything it signed becomes invalid. Continue?", ask,
        ):
            console.print("Cancelled.")
            return 1
        res = ak.revoke(args.kid, reason=args.reason)
        if res.get("status") == "revoked":
            tail = " A new key will be made on the next boot." if res.get("was_active") else ""
            console.print(f"[green]✓ Revoked[/green] {args.kid}.{tail}")
        return _report(res)

    console.print(f"Unknown action {action!r}")
    return 2


def main(args: argparse.Namespace) -> None:
    code = cmd_agent_key(args)
    if code:
        sys.exit(code)
