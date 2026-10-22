"""Memory collapse alert: when startup sets an unreadable memory file aside and starts a fresh one, say so AT ONCE.

Windy 0, 10-19 04:09Z: the agent quarantined its 70 MB memory and ran on a fresh, near-empty one for two days
before anyone noticed (only an ERROR log line). Now:
  - ``write_marker`` (called by Database._quarantine, the moment it happens) leaves ``<db>.collapse.json`` next
    to the database: file names, sizes and the reason only, never memory content;
  - ``announce`` (called once the maintenance loop has the database open, in every process) CLAIMS the marker
    with an atomic rename, so only one process announces: one ERROR log line, one owner mail (Windy Mail, when
    configured) and one line appended to ``WINDY_INCIDENT_BOARD_FILE`` when that is set.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ENV_BOARD = "WINDY_INCIDENT_BOARD_FILE"


def marker_path(db_path: str | Path) -> Path:
    p = Path(db_path)
    return p.with_name(p.name + ".collapse.json")


def write_marker(db_path: str | Path, moved_to: str | Path, reason: str) -> None:
    """Never raises: an alert must not stop the agent from starting."""
    try:
        moved = Path(moved_to)
        data = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "file": Path(db_path).name,
                "moved_to": moved.name, "moved_bytes": moved.stat().st_size if moved.exists() else None,
                "reason": str(reason)[:120]}
        m = marker_path(db_path)
        tmp = m.with_name(m.name + f".tmp-{os.getpid()}")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, m)
    except Exception as exc:  # noqa: BLE001
        logger.error("memory collapse marker could not be written: %s", type(exc).__name__)


def _last_backup() -> dict[str, Any]:
    try:
        from windyfly.cloud_backup import get_backup_state

        s = get_backup_state()
        return {k: s.get(k) for k in ("last_backup", "backup_id", "episodes") if s.get(k) is not None}
    except Exception:  # noqa: BLE001
        return {}


def message(info: dict[str, Any]) -> str:
    last = _last_backup()
    size = info.get("moved_bytes")
    lines = [
        f"Memory collapse at {info.get('at')}: {info.get('file')} could not be read ({info.get('reason')}).",
        f"It was set aside as {info.get('moved_to')}"
        + (f" ({size:,} bytes)" if isinstance(size, int) else "")
        + ", kept for recovery, and the agent started a FRESH, empty memory.",
    ]
    if last:
        lines.append("Last cloud backup: " + ", ".join(f"{k}={v}" for k, v in last.items()) + ".")
    lines.append("Nothing is deleted. Restore or recover before the fresh memory replaces the backups.")
    return " ".join(lines)


def announce(db_path: str | Path) -> bool:
    """If a collapse marker is waiting, claim it (one process only) and raise the alarm. True if this call did."""
    m = marker_path(db_path)
    if not m.exists():
        return False
    claimed = m.with_name(m.name + f".announced-{int(time.time())}-{os.getpid()}")
    try:
        os.rename(m, claimed)  # atomic: a second process loses the race and stays quiet
    except OSError:
        return False
    try:
        info = json.loads(claimed.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {"file": Path(db_path).name, "reason": "marker unreadable"}
    text = message(info)
    first = not info.get("announced")
    (logger.error if first else logger.warning)(
        "MEMORY COLLAPSE%s: %s", "" if first else " (retrying the owner mail)", text)
    board = os.environ.get(ENV_BOARD, "").strip()
    if first and board and os.access(board, os.W_OK):
        try:
            with open(board, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                         f"Windy Agent (automatic, memory collapse): {text}\n")
        except OSError as exc:
            logger.error("memory collapse: BOARD line not written (%s)", type(exc).__name__)
    sent = False
    try:
        from windyfly.agent import owner_mail

        if owner_mail.configured():
            sent = owner_mail.send_sync("Your agent's memory could not be read: it started fresh", text)
            if not sent:
                # Mail is set up but the send failed (Mail down, a 5xx): put the marker back so the NEXT start
                # tries again (once per start), unless a newer collapse already wrote one.
                if not m.exists():
                    info["announced"] = True  # the BOARD line and the ERROR are not repeated on the retry
                    claimed.write_text(json.dumps(info), encoding="utf-8")
                    os.rename(claimed, m)
                logger.error("memory collapse: owner mail failed; it will be retried at the next start")
    except Exception as exc:  # noqa: BLE001
        logger.error("memory collapse: owner mail not sent (%s)", type(exc).__name__)
    return True
