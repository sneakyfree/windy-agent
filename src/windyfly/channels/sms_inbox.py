"""Inbound texts from Windy Text (dark: rides WINDY_TEXT_BYO=1, off by default).

Polls GET {WINDY_TEXT_BASE_URL}/sms/inbox/mine with the agent's own EPT (a 24 h
buffer; items carry message_sid, body, channel:"sms", from_kind owner|contact).

Rules (Windy Hub, 10-01):
- from_kind=contact: NEVER acted on. It is only relayed to the owner's DM (stranger
  band, no tools, no agent turn); any reply goes through send_sms's own gates.
- from_kind=owner: answered like a DM, but in the USER band, so no side-effecting
  tool can run from a text; the reply goes to the owner's DM, which is where the
  owner confirms anything with side effects.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

POLL_S = 60.0
_SEEN_CAP = 500


def fetch_inbox(base_url: str, ept: str, *, timeout: float = 10.0) -> list[dict[str, Any]]:
    resp = httpx.get(f"{base_url}/sms/inbox/mine",
                     headers={"Authorization": f"Bearer {ept}"}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    msgs = data.get("messages") if isinstance(data, dict) else None
    return [m for m in (msgs or []) if isinstance(m, dict)]


class SeenStore:
    """message_sids already handled, persisted so a restart never re-handles a text."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._seen: list[str] = []
        if path and path.exists():
            try:
                self._seen = list(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                self._seen = []

    def __contains__(self, sid: str) -> bool:
        return sid in self._seen

    def add(self, sid: str) -> None:
        self._seen.append(sid)
        self._seen = self._seen[-_SEEN_CAP:]
        if self._path:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._seen), encoding="utf-8")
                os.replace(tmp, self._path)
            except OSError as e:
                logger.warning("sms inbox: could not persist seen ids: %s", e)


def contact_relay(body: str) -> str:
    return (
        "New text from a contact (I have not replied or acted on it):\n"
        f"“{body.strip()[:1500]}”"
    )


OWNER_NOTE = (
    "\n\n(You texted me. I can't take actions from a text; reply here to confirm anything.)"
)


async def handle_new(
    items: list[dict[str, Any]],
    seen: SeenStore,
    *,
    send_dm: Callable[[str], Awaitable[None]],
    run_owner_turn: Callable[[str], Awaitable[str]],
) -> int:
    """Handle unseen texts in order. Returns how many were handled."""
    handled = 0
    for item in items:
        sid = str(item.get("message_sid") or "")
        if not sid or sid in seen:
            continue
        body = str(item.get("body") or "")
        seen.add(sid)  # mark first: a crash must never re-act on a text
        if item.get("from_kind") == "owner":
            reply = await run_owner_turn(body)
            await send_dm(reply + OWNER_NOTE)
        else:
            await send_dm(contact_relay(body))
        handled += 1
    return handled
