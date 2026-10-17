"""The owner-facing soul import (strand P2.21.13): /soul import -> send the file -> preview -> approve or reject.

One pending import per owner conversation (``key`` = "<platform>:<channel_id>", the same pair the command
context carries). Every step answers in plain words; nothing is written to memory before /soul approve,
and the uploaded file is wiped after approve, after reject, or when the import is 30 minutes old.

Nothing here saves a conversation episode: command replies and the preview go straight back to the
channel, never through the agent's turn loop, so no export text reaches a later model prompt (Hub's
review A1). Logs carry counts only.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, BinaryIO

from windyfly.soul_import import intake as _intake

logger = logging.getLogger(__name__)

PENDING_FOR_S = _intake.MAX_AGE_S
TELEGRAM_MAX_BYTES = 20 * 1024 * 1024      # the most a Telegram bot can download
MATRIX_MAX_BYTES = 25 * 1024 * 1024        # Windy Chat's Synapse max_upload_size (Chat, 10-17)


@dataclass
class _Pending:
    started: float = field(default_factory=time.time)
    intake: _intake.Intake | None = None
    source: str | None = None


_pending: dict[str, _Pending] = {}
_lock = threading.Lock()


def _get(key: str) -> _Pending | None:
    with _lock:
        p = _pending.get(key)
        if p and time.time() - p.started > PENDING_FOR_S:
            _pending.pop(key, None)
            _intake.discard(p.intake)
            return None
        return p


def _drop(key: str) -> None:
    with _lock:
        p = _pending.pop(key, None)
    if p:
        _intake.discard(p.intake)


def awaiting_file(key: str) -> bool:
    """True when this owner conversation asked to import and the file has not arrived yet."""
    p = _get(key)
    return bool(p and p.intake is None)


def start(key: str) -> str:
    _drop(key)
    with _lock:
        _pending[key] = _Pending()
    return ("Send me your export as a file here, in this chat: a ChatGPT conversations.json, or a zip of an "
            "OpenClaw, Hermes or ChatGPT export. Up to 25 MB on Windy Chat and 20 MB on Telegram; a bigger "
            "ChatGPT export: send only its conversations.json. Nothing is kept unless you approve, and the "
            "file is wiped either way. /soul reject cancels.")


def too_big(size: int | None, limit: int, where: str) -> str | None:
    if size is not None and size > limit:
        return (f"That file is {size // (1024 * 1024)} MB, more than {where} lets me receive "
                f"({limit // (1024 * 1024)} MB). Send only the conversations.json from the export.")
    return None


def take_file(db: Any, key: str, stream: BinaryIO, filename: str, *, max_bytes: int) -> str:
    """The awaited file arrived: receive it safely and answer with the preview (nothing written)."""
    from windyfly.soul_import.orchestrator import import_soul

    p = _get(key)
    if p is None or p.intake is not None:
        return "I wasn't expecting a file. Start with /soul import."
    try:
        got = _intake.receive(stream, filename=filename, max_bytes=max_bytes)
    except _intake.IntakeError as e:
        _drop(key)
        return f"I couldn't take that file: {e}. Nothing was kept. /soul import to try again."
    result = import_soul(db, str(got.dir), user_approved=False)
    if result.get("error") or not result.get("preview"):
        _intake.discard(got)
        _drop(key)
        return "I couldn't recognise that as an export I know. Nothing was kept."
    with _lock:
        p.intake, p.source = got, result.get("source_type")
    logger.info("soul import: previewed (%s)", p.source)
    return (result["preview"].rsplit("\n", 1)[0].rstrip()
            + "\n\nType /soul approve to keep it (sensitive items wait for your review), or /soul reject.")


def approve(db: Any, key: str) -> str:
    from windyfly.soul_import.orchestrator import import_soul

    p = _get(key)
    if p is None or p.intake is None:
        return "There is no import waiting. Start with /soul import."
    try:
        result = import_soul(db, str(p.intake.dir), p.source, user_approved=True)
    finally:
        _drop(key)
    if result.get("error"):
        return "The import failed and nothing was kept. The file is wiped."
    imported, flagged, skipped = (result.get("imported", 0), result.get("flagged", 0), result.get("skipped", 0))
    logger.info("soul import: approved (%s) imported=%d flagged=%d skipped=%d", p.source, imported, flagged, skipped)
    return (f"Imported {imported} item{'s' if imported != 1 else ''}. "
            f"{flagged} sensitive item{'s wait' if flagged != 1 else ' waits'} for your review and "
            f"{'are' if flagged != 1 else 'is'} not used until you keep {'them' if flagged != 1 else 'it'}. "
            f"{skipped} skipped. The file is wiped.")


def reject(key: str) -> str:
    had = _get(key) is not None
    _drop(key)
    return ("Cancelled. Nothing was kept, and the file is wiped." if had
            else "There is no import waiting.")
