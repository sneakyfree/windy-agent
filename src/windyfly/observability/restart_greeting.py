"""Pending-greeting flag for after-restart UX.

When grandma triggers the nuclear reset (panic phrase), the bot
SIGTERMs itself and the process dies. ``Restart=always`` revives it
seconds later — but the new process has no memory that grandma was
just here. From her side, she sees the "🆘 Got it. Resetting…"
message and then nothing — radio silence.

This module is the bridge across the restart. When panic fires,
write the chat_id to a flag file. When the new process starts up
and finishes channel registration, check the flag, send "I'm back!"
to that chat_id, and clear the flag.

The flag file lives in ~/.windy/ (the same dir as the env file and
log) so it survives the process restart but not a full reinstall.

Single-line JSON keeps the format easy to debug; the file is small
enough (<200 bytes) that atomic writes are trivial.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _flag_path() -> Path:
    """Where the pending-greeting flag lives."""
    base = os.environ.get("WINDY_PENDING_GREETING_DIR", "/home/grantwhitmer/.windy")
    return Path(base) / ".pending_restart_greeting"


def set_pending_greeting(
    chat_id: str,
    platform: str = "telegram",
    reason: str = "panic_reset",
) -> None:
    """Record that we owe the user a "back online" message.

    Called from the panic handler immediately before scheduling the
    self-restart. Best-effort: if the write fails (disk full, perms),
    we log and move on — losing a greeting is better than losing the
    restart.
    """
    payload = {
        "chat_id": chat_id,
        "platform": platform,
        "reason": reason,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    path = _flag_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write so a torn write can't leave a half-flag.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload) + "\n")
        tmp.replace(path)
    except Exception as e:
        logger.warning("set_pending_greeting failed: %s", e)


def consume_pending_greeting() -> dict | None:
    """If a greeting is pending, return it and CLEAR the flag.

    Called from main.py once the channels are up. Returns None if no
    greeting is queued (the common case — every fresh start). Clears
    the flag immediately so a crash mid-greeting doesn't loop the
    same notification on every restart attempt.
    """
    path = _flag_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        logger.warning("could not parse pending-greeting flag: %s", e)
        try:
            path.unlink()
        except Exception:
            pass
        return None
    # Clear before returning. If the send fails after, that's still
    # better than spamming a greeting every restart.
    try:
        path.unlink()
    except Exception as e:
        logger.warning("could not clear pending-greeting flag: %s", e)
    return data


GREETING_TEXT = (
    "✨ I'm back! Your memory and personality are intact — I just "
    "wiped this conversation thread to fix whatever was wrong. "
    "What were we working on?"
)
