"""Inbox watch — the agent notices mail addressed to it.

Until 2026-09-05 nothing in windy-agent ever *read* the agent's Windy
Mail inbox on its own: ``list_inbox`` was an LLM tool the owner had to
ask for, and Mail's inbound webhook wrote rows "for agent polling" that
no code polled. An email sent to ``<agent>@windymail.ai`` therefore never
woke anyone (one inbound row in production, ever, 2026-07-11).

This module is the poll. It runs as a maintenance job (see
``agent/maintenance.py``): every ``DEFAULT_INTERVAL_S`` it fetches the
unread inbox through the existing ``WindyMailAdapter`` (same auth, same
rate limits), remembers what it has seen in one JSON file in the state
dir, and hands anything new to a channel-provided ``notify`` callable
that tells the owner. v1 deliberately notifies rather than answering:
the owner replies in chat and the normal agent turn takes it from there.

Rules:
  - first run seeds the seen-set and stays silent, so a fresh install
    does not announce a month of backlog;
  - at most ``MAX_NOTIFY_PER_TICK`` messages per notice;
  - never raises into the scheduler; a Mail outage is one log line;
  - cross-process dedup comes free from the maintenance last-run file
    (telegram and matrix share the state dir, one of them polls).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from windyfly.platform import windy_state_dir

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 300
MAX_NOTIFY_PER_TICK = 5
MAX_SEEN = 500
STATE_FILE = "inbox-watch.json"

NotifyFn = Callable[[str], Any]


def _state_path(state_dir: Path | None = None) -> Path:
    return (state_dir or windy_state_dir()) / STATE_FILE


def load_state(state_dir: Path | None = None) -> dict[str, Any]:
    try:
        return json.loads(_state_path(state_dir).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict[str, Any], state_dir: Path | None = None) -> None:
    try:
        path = _state_path(state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # a state write must never break the poll
        logger.debug("inbox-watch state write failed: %s", exc)


def message_key(message: dict[str, Any]) -> str:
    """Stable identity for a message across polls."""
    for k in ("id", "message_id", "messageId", "uid"):
        v = message.get(k)
        if v:
            return str(v)
    raw = "|".join(
        str(message.get(k, "")) for k in ("from", "sender", "subject", "date", "received_at")
    )
    return "h:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _sender(message: dict[str, Any]) -> str:
    v = message.get("from") or message.get("sender") or ""
    if isinstance(v, dict):
        return v.get("email") or v.get("name") or ""
    if isinstance(v, list) and v:
        first = v[0]
        return first.get("email", "") if isinstance(first, dict) else str(first)
    return str(v)


def poll_new_messages(
    adapter: Any,
    *,
    state_dir: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fetch unread mail and return only messages not seen before.

    The first poll ever seeds the seen-set and returns [] — silence on a
    backlog is the safe direction.
    """
    now = now or datetime.now(timezone.utc)
    state = load_state(state_dir)
    seen: list[str] = list(state.get("seen", []))
    seen_set = set(seen)
    first_run = "last_poll" not in state

    messages = adapter.check_inbox(unread_only=True) or []
    fresh: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        key = message_key(m)
        if key in seen_set:
            continue
        seen_set.add(key)
        seen.append(key)
        fresh.append(m)

    state["seen"] = seen[-MAX_SEEN:]
    state["last_poll"] = now.isoformat()
    state["last_error"] = str(getattr(adapter, "last_error", "") or "")
    state["last_new"] = len(fresh)
    save_state(state, state_dir)

    if first_run and fresh:
        logger.info("inbox-watch: seeded %d unread message(s) silently", len(fresh))
        return []
    return fresh


def format_notice(messages: list[dict[str, Any]], *, agent_name: str = "") -> str:
    """Owner-facing summary of new mail. No bodies — subject + sender only."""
    shown = messages[:MAX_NOTIFY_PER_TICK]
    who = f" for {agent_name}" if agent_name else ""
    lines = [f"📬 New mail{who}: {len(messages)} message(s)"]
    for m in shown:
        subject = str(m.get("subject") or "(no subject)")[:80]
        lines.append(f"• {_sender(m) or 'unknown sender'} — {subject}")
    if len(messages) > len(shown):
        lines.append(f"… and {len(messages) - len(shown)} more")
    lines.append("Say “read my mail” and I’ll go through them.")
    return "\n".join(lines)


def make_inbox_watch_job(
    notify: NotifyFn,
    *,
    adapter_factory: Callable[[], Any | None] | None = None,
    interval_s: float = DEFAULT_INTERVAL_S,
    state_dir: Path | None = None,
    agent_name: str = "",
):
    """Build the maintenance job. ``adapter_factory`` is called on every
    tick so a mailbox provisioned after boot lights up without a restart."""
    from windyfly.agent.maintenance import MaintenanceJob, interval_due

    if adapter_factory is None:
        from windyfly.channels.email import get_email_adapter
        adapter_factory = get_email_adapter

    def _run() -> None:
        adapter = adapter_factory()
        if adapter is None:
            return  # no mailbox yet — nothing to watch
        try:
            fresh = poll_new_messages(adapter, state_dir=state_dir)
        except Exception as exc:  # noqa: BLE001 — never into the scheduler
            logger.warning("inbox-watch poll failed: %s", exc)
            return
        if not fresh:
            return
        try:
            notify(format_notice(fresh, agent_name=agent_name))
            logger.info("inbox-watch: notified owner of %d new message(s)", len(fresh))
        except Exception as exc:  # noqa: BLE001
            logger.warning("inbox-watch notify failed: %s", exc)

    return MaintenanceJob(name="inbox_watch", run=_run, due=interval_due(interval_s))
