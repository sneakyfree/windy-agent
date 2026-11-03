"""The agent's Windy Chat sync position, kept across restarts and sleeps (#687 F, the laptop-home MUST-FIX).

Without it, a restarted agent synced from "now": anything the owner wrote while the laptop slept was older than
matrix_bot's 120 s catch-up window and was silently skipped. With it, the first sync after a restart or a wake resumes
from the last position the agent finished, so every message that arrived meanwhile is answered once. The handled-events
record (channels/handled_events.py) makes sure a resumed sync never answers anything twice.

The position belongs to one Matrix user + device; a saved one for another account or device is ignored. The file is
0600 in the state dir. A position that can't be read means a fresh start (the old behaviour), never a crash.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_NAME = "matrix_sync_position.json"
ENV_VAR = "WINDY_MATRIX_SYNC_POSITION_FILE"
SAVE_EVERY_S = 5.0


def _path() -> Path:
    from windyfly.state_files import state_path

    return state_path(ENV_VAR, FILE_NAME)


def load(user_id: str, device_id: str) -> str | None:
    """The saved next_batch for this user + device, or None (fresh start)."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("sync position: unreadable (%s); starting fresh", type(exc).__name__)
        return None
    if not isinstance(data, dict) or data.get("user_id") != user_id or data.get("device_id") != device_id:
        return None
    token = data.get("next_batch")
    return token if isinstance(token, str) and token else None


def save(user_id: str, device_id: str, next_batch: str) -> bool:
    """Write the position (atomic, 0600). Never raises."""
    if not (user_id and device_id and next_batch):
        return False
    try:
        from windyfly.state_files import write_private

        write_private(_path(), json.dumps({"user_id": user_id, "device_id": device_id, "next_batch": next_batch,
                                           "saved_at": int(time.time())}).encode("utf-8"))
        return True
    except OSError as exc:
        logger.warning("sync position: not saved (%s)", type(exc).__name__)
        return False
