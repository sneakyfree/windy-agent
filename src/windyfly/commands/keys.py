"""``windy keys`` — rotate / inspect the wk_ bot credential.

Subcommands::

    windy keys show              # inspect cached wk_ key + expiry
    windy keys rotate            # mint a fresh wk_ key, revoke the old one
    windy keys rotate --hard     # also notify connected services

The auto-rotation path (triggered by the brain's daily tick) lives in
``windyfly.auth.bot_credentials.get_bot_key``; this module is the manual
operator surface documented in DEPLOY.md §4.2.

Idempotency: running ``rotate`` twice in a row mints twice. Each mint
atomically replaces the cached key; if the old-key revoke step fails,
the old key stays minted server-side but is no longer cached locally
and will expire on its own timeline. Safely abortable — any Ctrl-C
between mint and revoke leaves the new key valid.

What ``--hard`` does NOT do: there is no cascade receiver anywhere in
the ecosystem (measured 2026-08-10 — no ``bot_key.revoked`` handler in
windy-pro or windy-mail). The webhooks are posted for observability and
are expected to fail; a revoked key stops working when a platform next
revalidates it against windy-pro, not when this command runs. Do not
read "cascade acked" as "the key is dead everywhere".
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Callable

from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()


def cmd_keys(args: argparse.Namespace) -> None:
    """Dispatch ``windy keys <action>``."""
    action = getattr(args, "action", None) or "show"
    handler: Callable[[argparse.Namespace], None] = {
        "show":   _cmd_keys_show,
        "rotate": _cmd_keys_rotate,
    }.get(action, _cmd_keys_show)
    handler(args)


# ─── show ────────────────────────────────────────────────────────────


def _cmd_keys_show(_args: argparse.Namespace) -> None:
    from windyfly.auth.bot_credentials import _load_cached
    from datetime import datetime, timezone

    cred = _load_cached()
    if cred is None:
        console.print()
        console.print("  [dim]No cached wk_ bot key.[/dim]")
        console.print("  [dim]Mint one with [bold]windy keys rotate[/bold].[/dim]")
        console.print()
        return

    now = datetime.now(timezone.utc)
    remaining = cred.expires_at - now
    days_left = remaining.days
    band = (
        "[green]healthy[/green]" if days_left > 30 else
        "[yellow]rotating soon[/yellow]" if days_left > 7 else
        "[red]rotate now[/red]"
    )

    table = Table(title="Bot key", title_style="bold", border_style="cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("key_id",            cred.key_id or "- (NOT REVOCABLE)")
    table.add_row("windy_identity_id", cred.windy_identity_id or "-")
    table.add_row("passport_number",   cred.passport_number or "-")
    table.add_row("scopes",            ", ".join(cred.scopes) or "-")
    table.add_row("expires_at",        cred.expires_at.isoformat())
    table.add_row("days_remaining",    f"{days_left} days — {band}")

    console.print()
    console.print(table)
    if not cred.key_id:
        # Without the account-server's key id there is no way to revoke
        # this credential — the operator needs to know that now, not
        # during an incident.
        console.print(
            "  [yellow]⚠ No key_id cached — this key CANNOT be revoked from here.[/yellow]"
        )
        console.print(
            "  [dim]Rotate to replace it; the old one stays live until it expires.[/dim]"
        )
    console.print()


# ─── rotate ──────────────────────────────────────────────────────────


def _cmd_keys_rotate(args: argparse.Namespace) -> None:
    """Mint a fresh wk_ key and revoke the old one.

    Steps:
        1. Resolve owner JWT + passport (from env / cache).
        2. Mint a new key via Pro's POST /api/v1/identity/api-keys.
           Mint is atomic — the cache now points at the new key.
        3. Revoke the previous key_id via DELETE
           /api/v1/identity/api-keys/<key id>. Only a server-CONFIRMED
           revocation prints green. If hard=True, also post to the
           cascade webhooks (see the module docstring — nothing receives
           them today).
        4. Verify by re-reading the cache + hitting get_bot_key().

    Abortable: Ctrl-C between 2 and 3 leaves the new key valid and
    simply defers the revoke to whoever notices the dangling key_id.
    """
    hard = bool(getattr(args, "hard", False))

    from windyfly.auth.bot_credentials import (
        BotIdentityUnavailable,
        _load_cached,
        clear_cached_bot_key,
    )

    console.print()
    console.print("[bold cyan]🔑 Rotating wk_ bot key[/bold cyan]")

    # Step 1: resolve identity.
    cached = _load_cached()
    owner_jwt = os.environ.get("WINDY_JWT", "")
    passport = (
        (cached.passport_number if cached else "")
        or os.environ.get("ETERNITAS_PASSPORT", "")
    )

    if not owner_jwt:
        console.print("  [red]✗ WINDY_JWT not set — cannot mint without owner auth.[/red]")
        console.print("  [dim]Source your .env or re-login via Windy Pro first.[/dim]")
        console.print()
        sys.exit(2)
    if not passport:
        console.print(
            "  [red]✗ No passport_number found in cache or ETERNITAS_PASSPORT.[/red]"
        )
        console.print("  [dim]Run [bold]windy passport[/bold] to confirm the agent is hatched.[/dim]")
        console.print()
        sys.exit(2)

    had_previous_key = cached is not None
    previous_key_id = cached.key_id if cached else ""
    previous_scopes = list(cached.scopes) if cached else None

    # Step 2: mint. Atomic — if this fails we haven't touched anything.
    try:
        new_cred = asyncio.run(
            _mint(owner_jwt=owner_jwt, passport=passport, scopes=previous_scopes)
        )
    except KeyboardInterrupt:
        console.print("  [yellow]↩ Aborted before new key was minted — no change.[/yellow]")
        console.print()
        sys.exit(130)
    except BotIdentityUnavailable as exc:
        # A missing prerequisite, not a failed call — same class as "no
        # JWT" above, so same exit code and the same kind of hint.
        console.print(f"  [yellow]· Mint {exc}[/yellow]")
        console.print(
            "  [dim]Terminal-lane agents have no Windy Word bot identity. Set "
            "BOT_IDENTITY_ID, or hatch through the browser, which supplies it.[/dim]"
        )
        console.print()
        sys.exit(2)
    except Exception as exc:
        console.print(f"  [red]✗ Mint failed:[/red] {exc}")
        console.print("  [dim]No change to cached key.[/dim]")
        console.print()
        sys.exit(3)

    console.print(
        f"  [green]✓[/green] New key minted "
        f"(id={new_cred.key_id or '-'}, scopes={','.join(new_cred.scopes) or '-'}, "
        f"expires {new_cred.expires_at.date().isoformat()})"
    )

    # Step 3: revoke the old key. Non-fatal — new key is already active.
    if previous_key_id and previous_key_id != new_cred.key_id:
        cascade_webhooks = _cascade_webhooks() if hard else None
        try:
            summary = asyncio.run(_revoke(
                key_id=previous_key_id,
                owner_jwt=owner_jwt,
                cascade=cascade_webhooks,
            ))
        except KeyboardInterrupt:
            console.print(
                "  [yellow]↩ Aborted during revoke — new key active, old "
                f"key_id={previous_key_id} still present server-side.[/yellow]"
            )
            console.print()
            sys.exit(130)
        except Exception as exc:
            # Revoke is best-effort. Surface a warning but don't exit red.
            logger.warning("Revoke of %s failed: %s", previous_key_id, exc)
            console.print(
                f"  [yellow]⚠ Old key revoke failed:[/yellow] {exc}"
                f" [dim](new key is still active)[/dim]"
            )
        else:
            if summary.get("revoked"):
                console.print(f"  [green]✓[/green] Old key revoked (id={previous_key_id})")
            else:
                # An unconfirmed revoke is a LIVE credential, not a
                # cosmetic warning. Print the server's own account of
                # what happened and say what it means.
                logger.warning(
                    "Revoke of %s not confirmed (%s): %s",
                    previous_key_id, summary.get("status"), summary.get("detail"),
                )
                console.print(
                    f"  [red]✗ Old key NOT revoked[/red] "
                    f"[dim](id={previous_key_id}, status={summary.get('status') or 'unknown'})[/dim]"
                )
                console.print(f"    [dim]{summary.get('detail') or 'no detail from server'}[/dim]")
                console.print(
                    "    [yellow]The old key is still live until it expires — "
                    "revoke it from Windy Word by hand.[/yellow]"
                )
            if hard:
                failed = [u for u, status in summary.get("cascade", {}).items() if not _status_ok(status)]
                if failed:
                    console.print(
                        "  [yellow]⚠ Cascade webhook(s) did not ack:[/yellow] "
                        + ", ".join(failed)
                    )
                else:
                    console.print(
                        "  [green]✓[/green] Cascade webhooks acked "
                        "[dim](an ack is a delivery receipt, not proof any platform "
                        "dropped the key)[/dim]"
                    )
    elif previous_key_id:
        # Same key_id — idempotent rotation returned the existing key.
        console.print("  [dim]· Server returned the same key_id — nothing to revoke.[/dim]")
    elif had_previous_key:
        # A cached credential with no key_id: minted before the account
        # -server contract was fixed, or hand-written. It is a real,
        # live wk_ key that we have no handle to revoke. Never let that
        # read as "nothing to do".
        logger.warning("Previous cached key has no key_id — cannot revoke it")
        console.print(
            "  [red]✗ Previous key cannot be revoked — no key_id in the cache.[/red]"
        )
        console.print(
            "    [dim]It was cached before the account-server contract was fixed. "
            "It stays live until it expires; revoke it from Windy Word by hand.[/dim]"
        )
    else:
        console.print("  [dim]· No previous key to revoke.[/dim]")

    # Step 4: verify the cache is healthy.
    verify = _load_cached()
    if verify is None or verify.bot_key != new_cred.bot_key:
        # Should be impossible — mint_bot_key writes the cache atomically.
        console.print("  [red]✗ Cache verification failed — clearing.[/red]")
        clear_cached_bot_key()
        console.print()
        sys.exit(4)

    console.print("  [green]✓[/green] Cache updated + verified")
    console.print()


# ─── plumbing ────────────────────────────────────────────────────────


async def _mint(
    *, owner_jwt: str, passport: str, scopes: list[str] | None,
):
    from windyfly.auth.bot_credentials import mint_bot_key
    return await mint_bot_key(
        owner_jwt=owner_jwt,
        passport_number=passport,
        scopes=scopes,
    )


async def _revoke(
    *, key_id: str, owner_jwt: str, cascade: list[str] | None,
) -> dict:
    from windyfly.auth.bot_credentials import revoke_bot_key
    return await revoke_bot_key(
        key_id=key_id,
        reason="rotated_via_windy_keys_rotate",
        owner_jwt=owner_jwt,
        cascade_webhook_urls=cascade,
    )


def _cascade_webhooks() -> list[str]:
    """Webhook URLs that would tell connected services to drop cached auth.

    Aspirational, and honestly so: no service in the ecosystem receives
    `bot_key.revoked` today (grepped 2026-08-10 — windy-pro and
    windy-mail have no such route), so every one of these is expected to
    404. They are posted for observability, and the caller is told an
    ack means "delivered", not "key dropped".

    Matrix is deliberately NOT in this list. It used to be, pointed at
    `/_matrix/client/versions` — an unauthenticated version probe that
    answers 200 to anyone. Synapse has no flush-bot-cache hook, so that
    entry could only ever manufacture a green "cascade acked" line for
    work nobody did.
    """
    webhooks: list[str] = []
    mail = os.environ.get("WINDYMAIL_API_URL", "").rstrip("/")
    if mail:
        webhooks.append(f"{mail}/api/v1/internal/bot-key-revoked")
    cloud = os.environ.get("WINDY_CLOUD_URL", "").rstrip("/")
    if cloud:
        webhooks.append(f"{cloud}/api/v1/internal/bot-key-revoked")
    return webhooks


def _status_ok(status: int | str) -> bool:
    """True when a cascade webhook's stored status represents success."""
    if isinstance(status, int):
        return 200 <= status < 300
    return False
