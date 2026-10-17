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


GUEST_OFF = "Soul import is off while guest mode is on. /guest off first."


def _guest() -> bool:
    """Guest mode (a demo for other people): no import, review, keep or drop (the design, Hub on #586)."""
    from windyfly.agent.guest_mode import is_guest_active

    return is_guest_active()


def awaiting_file(key: str) -> bool:
    """True when this owner conversation asked to import and the file has not arrived yet."""
    if _guest():
        return False
    p = _get(key)
    return bool(p and p.intake is None)


def start(key: str) -> str:
    if _guest():
        return GUEST_OFF
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

    if _guest():
        _drop(key)
        return GUEST_OFF
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

    if _guest():
        return GUEST_OFF
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


# ── /soul review: flagged imports wait here until the owner keeps or drops them (P2.21.11) ──────

_review_lists: dict[str, tuple[float, list[str]]] = {}
_drops: dict[str, tuple[float, list[str]]] = {}
REVIEW_LIST_FOR_S = 600


def _flagged(db: Any) -> list[dict[str, Any]]:
    return db.fetchall(
        "SELECT id, type, created_at, metadata FROM nodes "
        "WHERE metadata IS NOT NULL AND json_valid(metadata) "
        "AND COALESCE(json_extract(metadata, '$.needs_review'), 0) != 0 "
        "ORDER BY created_at, rowid")


def _numbered(key: str, nums: list[str]) -> list[str] | str:
    with _lock:
        at, ids = _review_lists.get(key, (0.0, []))
    if not ids or time.time() - at > REVIEW_LIST_FOR_S:
        return "List them first: /soul review."
    if nums == ["all"]:
        return list(ids)
    try:
        picked = sorted({int(n) for n in nums})
    except ValueError:
        return "Give the numbers from /soul review (for example /soul keep 1 3), or 'all'."
    if not picked or picked[0] < 1 or picked[-1] > len(ids):
        return f"Pick numbers from 1 to {len(ids)}."
    return [ids[n - 1] for n in picked]


def review(db: Any, key: str, nums: list[str]) -> str:
    """List flagged items by number and kind only; '/soul review <n>' opens one item's text."""
    import json

    if _guest():
        return GUEST_OFF
    rows = _flagged(db)
    if not nums:
        if not rows:
            return "Nothing is waiting for your review."
        with _lock:
            _review_lists[key] = (time.time(), [r["id"] for r in rows])
            _drops.pop(key, None)
        lines = [f"{len(rows)} imported item{'s' if len(rows) != 1 else ''} wait for your review "
                 "(not used until you keep them):"]
        for i, r in enumerate(rows, 1):
            src = str((json.loads(r["metadata"]) or {}).get("import_source", "import")).replace("imported_", "")
            lines.append(f"  {i}. {r['type']} from {src}, {str(r['created_at'])[:10]}")
        lines.append("/soul review <number> shows one. /soul keep <numbers> uses them; /soul drop <numbers|all> "
                     "deletes them for good.")
        return "\n".join(lines)
    picked = _numbered(key, nums[:1])
    if isinstance(picked, str):
        return picked
    row = db.fetchone("SELECT type, metadata FROM nodes WHERE id = ?", (picked[0],))
    if not row:
        return "That item is gone. /soul review lists what is left."
    text = str((json.loads(row["metadata"]) or {}).get("raw_content", ""))[:600]
    return f"{nums[0]}. {row['type']}: {text}\n/soul keep {nums[0]} or /soul drop {nums[0]}."


def keep(db: Any, key: str, nums: list[str]) -> str:
    import json

    if _guest():
        return GUEST_OFF
    picked = _numbered(key, nums)
    if isinstance(picked, str):
        return picked
    kept = 0
    for node_id in picked:
        row = db.fetchone("SELECT metadata FROM nodes WHERE id = ?", (node_id,))
        if not row:
            continue
        meta = json.loads(row["metadata"]) or {}
        meta.pop("needs_review", None)
        meta["reviewed"] = "kept"
        db.execute("UPDATE nodes SET metadata = ?, confidence = 0.5, epistemic_status = 'inferred', "
                   "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(meta), node_id))
        kept += 1
    db.commit()
    with _lock:
        _review_lists.pop(key, None)
    logger.info("soul review: kept %d", kept)
    return f"Kept {kept} item{'s' if kept != 1 else ''}; I can use {'them' if kept != 1 else 'it'} now."


def _describe(db: Any, ids: list[str]) -> list[str]:
    import json

    out = []
    for node_id in ids:
        r = db.fetchone("SELECT type, created_at, metadata FROM nodes WHERE id = ?", (node_id,))
        if r:
            src = str((json.loads(r["metadata"]) or {}).get("import_source", "import")).replace("imported_", "")
            out.append(f"{r['type']} from {src}, {str(r['created_at'])[:10]}")
    return out


def drop(db: Any, key: str, nums: list[str]) -> str:
    """Ask first: the list shows no text, so a mistyped number would delete an item never seen (Hub on #586)."""
    if _guest():
        return GUEST_OFF
    picked = _numbered(key, nums)
    if isinstance(picked, str):
        return picked
    with _lock:
        at, ids = _review_lists.get(key, (0.0, []))
        _drops[key] = (time.time(), list(picked))
    n = len(picked)
    lines = [f"Delete {n} item{'s' if n != 1 else ''} for good? This can't be undone."]
    lines += [f"  {ids.index(i) + 1}. {d}" for i, d in zip(picked, _describe(db, picked)) if i in ids]
    lines.append("/soul yes deletes them. /soul review starts over and deletes nothing.")
    return "\n".join(lines)


def confirm_drop(db: Any, key: str) -> str:
    from windyfly.memory import forget as _forget

    if _guest():
        return GUEST_OFF
    with _lock:
        at, ids = _drops.pop(key, (0.0, []))
    if not ids or time.time() - at > REVIEW_LIST_FOR_S:
        return "Nothing is waiting to be deleted. /soul review, then /soul drop <numbers>."
    # Only items still flagged: anything kept since the question stays.
    still = {r["id"] for r in _flagged(db)}
    items = [_forget.Item("memory", node_id, "", "") for node_id in ids if node_id in still]
    counts = _forget.forget(db, items)
    with _lock:
        _review_lists.pop(key, None)
    n = counts.get("memory", 0)
    logger.info("soul review: dropped %d", n)
    return f"Deleted {n} item{'s' if n != 1 else ''} for good. No readable copy is left in my memory file."
