"""Guest mode — flip the bot into grandma-mode for a tour demo.

When Grant is about to demo Windy Fly to a non-technical audience,
``/guest on`` rewires every incoming message to ``Band.USER``, which
triggers GRANDMA MODE in prompt assembly (added in PR #118 — short,
plain-English replies, no infrastructure jargon). After the demo,
``/guest off`` restores the OWNER tone Grant uses day-to-day.

Design choices:
  - File-based flag at ``~/.windy/.guest`` so it survives restart.
    Forgotten-on guest mode just means slightly friendlier replies,
    which is harmless. Forgotten-off would mean engineering jargon
    in front of an audience.
  - No expiry. Pause has reasons to auto-clear (cost). Guest doesn't.
    Grant turns it off explicitly when the demo ends.
  - Atomic writes (.tmp + rename) — same pattern as ``pause()`` so a
    half-written flag can't bork the bot.

This is the channel-side switch. The actual tone change lives in
``windyfly.agent.prompt.assemble_prompt`` (already accepts a band
kwarg). All ``main.py`` does is consult ``is_guest_active()`` per
message and pass ``Band.USER`` instead of ``Band.OWNER`` when on.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _guest_flag_path() -> Path:
    return Path(os.environ.get(
        "WINDY_GUEST_FLAG",
        "/home/grantwhitmer/.windy/.guest",
    ))


def is_guest_active() -> bool:
    """Quick check at the top of the channel _respond hook. File-based
    so the check is one stat call — no DB, no parse on the hot path
    when the flag isn't set."""
    return _guest_flag_path().exists()


def guest_status() -> dict[str, Any]:
    """Return the guest-mode state with metadata.

    Returns:
        {
          "active":     bool,
          "enabled_at": ISO timestamp or None,
          "actor":      str or None,
          "label":      str or None,  # optional demo-context label
        }
    """
    path = _guest_flag_path()
    if not path.exists():
        return {"active": False, "enabled_at": None, "actor": None, "label": None}
    try:
        data = json.loads(path.read_text())
    except Exception:
        # Torn flag — treat as active (the file is there) but with
        # no metadata. Better than silently dropping out of demo
        # mode mid-stage.
        return {"active": True, "enabled_at": None, "actor": None, "label": None}
    return {
        "active": True,
        "enabled_at": data.get("enabled_at"),
        "actor": data.get("actor"),
        "label": data.get("label"),
    }


def guest_on(actor: str = "user", label: str | None = None) -> dict[str, Any]:
    """Enable guest mode. Atomic write (.tmp + rename)."""
    path = _guest_flag_path()
    payload: dict[str, Any] = {
        "enabled_at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
    }
    if label:
        payload["label"] = label
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload) + "\n")
        tmp.replace(path)
        logger.warning(
            "GUEST MODE ON: actor=%s label=%s — replies now grandma-mode",
            actor, label or "-",
        )
        return {"ok": True, "active": True, **payload}
    except Exception as e:
        logger.error("guest flag write failed: %s", e)
        return {"ok": False, "error": str(e)}


def guest_off() -> dict[str, Any]:
    """Disable guest mode. Best-effort — missing flag is a no-op."""
    path = _guest_flag_path()
    existed = path.exists()
    try:
        path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("guest_off failed to delete flag: %s", e)
        return {"ok": False, "error": str(e)}
    logger.info("GUEST MODE OFF: flag cleared (existed=%s)", existed)
    return {"ok": True, "was_active": existed}
