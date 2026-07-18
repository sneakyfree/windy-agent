"""The Journal — the dated index over the Chronicle (Doctrine Build 3).

The Chronicle (raw episodes) keeps every word forever; the Journal is
its table of contents. One dated entry per "chapter" (a stretch of
conversation, split on a >1h idle gap so a normal day is one entry and
a marathon day is several). Each entry = a short prose summary + bullet
key points + tagged entities (people/places/topics). Two-tier reading:
scroll the Journal to find the right afternoon, then read that stretch
of raw Chronicle for the full nuance.

Doctrine alignment:
- **Index, NOT abbreviation.** The Journal never replaces the
  transcript — raw is untouched and authoritative (Law 1). A Journal
  entry being wrong or thin is harmless: the raw is right there.
- **Disposable, rebuildable cache** (Law 9). If a model wasn't
  available, the entry is written from a deterministic extractive
  skeleton and marked ``enriched=False`` so a later pass can upgrade
  it. It NEVER hard-depends on a live model — same rule as turnover.
- **Passes the razor:** it RECORDS an index of what happened; it does
  not judge what is worthy of KEEPING (the raw keeps everything).

Entries are stored as ``journal_entry`` nodes keyed by
``journal:<date>:<chapter>`` so re-running a day is idempotent
(upsert). The write path is scheduled (a daily timer), never inline in
agent_respond — grandma's turn latency is never taxed by journaling.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

# A gap longer than this between consecutive turns ends a chapter.
_CHAPTER_GAP_SECONDS = 3600  # 1 hour (Doctrine: the idle-gap boundary)
_MAX_SUMMARY_CHARS = 600
_MAX_BULLET_CHARS = 140
_MAX_BULLETS = 8
_PREVIEW_CHARS = 100

# Optional model caller: (messages, *, max_tokens) -> str. Injected so
# tests run without a live model and so the write path degrades to the
# deterministic skeleton when no model is available.
ModelCaller = Callable[..., str]


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw[:26], fmt)
        except ValueError:
            continue
    return None


def _split_chapters(episodes: list[dict]) -> list[list[dict]]:
    """Split chronologically-ordered episodes on >1h idle gaps."""
    chapters: list[list[dict]] = []
    current: list[dict] = []
    prev_ts: datetime | None = None
    for ep in episodes:
        ts = _parse_ts(ep.get("created_at"))
        if (
            prev_ts is not None
            and ts is not None
            and (ts - prev_ts).total_seconds() > _CHAPTER_GAP_SECONDS
        ):
            if current:
                chapters.append(current)
            current = []
        current.append(ep)
        prev_ts = ts or prev_ts
    if current:
        chapters.append(current)
    return chapters


def _extractive_skeleton(chapter: list[dict]) -> dict[str, Any]:
    """Deterministic, no-LLM entry — always available (Law 9 fallback)."""
    user_lines = [
        (e.get("content") or "").strip().replace("\n", " ")
        for e in chapter if e.get("role") == "user"
    ]
    user_lines = [u for u in user_lines if u]
    bullets = [f"{u[:_MAX_BULLET_CHARS]}" for u in user_lines[:_MAX_BULLETS]]
    summary = (
        f"{len(chapter)} turns."
        + (f" Started: {user_lines[0][:_PREVIEW_CHARS]}" if user_lines else "")
    )
    return {
        "summary": summary[:_MAX_SUMMARY_CHARS],
        "bullets": bullets,
        "entities": [],
        "enriched": False,
    }


_ENRICH_INSTRUCTION = (
    "You are writing one dated JOURNAL ENTRY indexing a stretch of a "
    "conversation between an AI companion and its person. Return STRICT "
    "JSON with keys: summary (<=2 sentences, plain, warm, past tense), "
    "bullets (array of <=8 short key points — decisions, facts, tasks, "
    "promises), entities (array of short tags: people, places, topics, "
    "e.g. 'Fred', 'county fair', 'quilt raffle'). This is an INDEX so a "
    "future reader can find this day fast — it is NOT a replacement for "
    "the transcript. No preamble, JSON only."
)


def _enrich_with_model(
    chapter: list[dict], model_caller: ModelCaller,
) -> dict[str, Any] | None:
    """LLM-produced summary/bullets/entities. None on any failure —
    caller falls back to the extractive skeleton."""
    transcript = "\n".join(
        f"{e.get('role')}: {(e.get('content') or '').strip()[:500]}"
        for e in chapter
    )[:12000]
    try:
        raw = model_caller(
            [
                {"role": "system", "content": _ENRICH_INSTRUCTION},
                {"role": "user", "content": transcript},
            ],
            max_tokens=700,
        )
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.split("```")[1] if "```" in text[3:] else text[3:]
            text = text.lstrip("json").strip()
        data = json.loads(text)
        summary = str(data.get("summary", ""))[:_MAX_SUMMARY_CHARS]
        bullets = [
            str(b)[:_MAX_BULLET_CHARS]
            for b in (data.get("bullets") or [])
        ][:_MAX_BULLETS]
        entities = [str(x)[:60] for x in (data.get("entities") or [])][:20]
        if not summary and not bullets:
            return None
        return {
            "summary": summary,
            "bullets": bullets,
            "entities": entities,
            "enriched": True,
        }
    except Exception as e:  # noqa: BLE001 — enrichment is best-effort
        logger.debug("journal enrichment failed, using skeleton: %s", e)
        return None


def compose_day_entries(
    db: Any,
    day: str,
    *,
    model_caller: ModelCaller | None = None,
) -> list[dict[str, Any]]:
    """Build the Journal chapters for a single calendar day (UTC).

    ``day`` is 'YYYY-MM-DD'. Returns a list of entry dicts (one per
    idle-gap chapter), each ready for ``write_day``.
    """
    rows = db.fetchall(
        "SELECT role, content, session_id, created_at FROM episodes "
        "WHERE date(created_at) = ? ORDER BY created_at ASC, rowid ASC",
        (day,),
    ) or []
    if not rows:
        return []

    entries: list[dict[str, Any]] = []
    for idx, chapter in enumerate(_split_chapters(list(rows))):
        body = None
        if model_caller is not None:
            body = _enrich_with_model(chapter, model_caller)
        if body is None:
            body = _extractive_skeleton(chapter)
        first_ts = chapter[0].get("created_at")
        last_ts = chapter[-1].get("created_at")
        entries.append({
            "date": day,
            "chapter": idx,
            "turn_count": len(chapter),
            "started_at": first_ts,
            "ended_at": last_ts,
            **body,
        })
    return entries


def write_day(
    db: Any,
    day: str,
    *,
    model_caller: ModelCaller | None = None,
) -> int:
    """Compose + persist all Journal entries for ``day`` (idempotent).

    Best-effort by contract: never raises into the caller. Returns the
    number of entries written.
    """
    try:
        from windyfly.memory.nodes import upsert_node
        entries = compose_day_entries(db, day, model_caller=model_caller)
        for entry in entries:
            upsert_node(
                db,
                type="journal_entry",
                name=f"journal:{day}:{entry['chapter']}",
                metadata={
                    **entry,
                    "written_at": datetime.now(timezone.utc).isoformat(),
                },
                epistemic_status="verified",
                confidence=1.0,
                source="journal",
            )
        if entries:
            logger.info(
                "journal: wrote %d chapter(s) for %s (enriched=%s)",
                len(entries), day, entries[0].get("enriched"),
            )
        return len(entries)
    except Exception as e:  # noqa: BLE001 — journaling never breaks the caller
        logger.warning("journal write failed for %s (non-fatal): %s", day, e)
        return 0


def read_entries(
    db: Any,
    *,
    limit: int = 14,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """Read Journal entries (newest first), for the journal.read tool."""
    where = "type = 'journal_entry'"
    params: list[Any] = []
    if since:
        where += " AND json_extract(metadata, '$.date') >= ?"
        params.append(since)
    if until:
        where += " AND json_extract(metadata, '$.date') <= ?"
        params.append(until)
    rows = db.fetchall(
        f"SELECT metadata FROM nodes WHERE {where} "  # noqa: S608
        "ORDER BY json_extract(metadata, '$.date') DESC, "
        "json_extract(metadata, '$.chapter') ASC LIMIT ?",
        (*params, int(limit)),
    ) or []
    out: list[dict[str, Any]] = []
    for row in rows:
        meta = row.get("metadata") if isinstance(row, dict) else None
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = None
        if isinstance(meta, dict):
            out.append({
                "date": meta.get("date"),
                "chapter": meta.get("chapter"),
                "summary": meta.get("summary"),
                "bullets": meta.get("bullets") or [],
                "entities": meta.get("entities") or [],
                "started_at": meta.get("started_at"),
                "ended_at": meta.get("ended_at"),
            })
    return out
