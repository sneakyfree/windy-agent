"""Find and delete what the agent remembers (strand N10.2; N10.1's history delete reuses ``forget``).

``find`` looks for the owner's words everywhere the agent keeps text, not only in the chat history:
facts and summaries (nodes, which include relationship moments, journal entries and the turnover
letter), messages, "Never Wrong Twice" corrections, goals, intents, memory conflicts, tool-call records
and the event log. A row matches when it contains every word. ``forget`` deletes exactly the rows it is
given and leaves no readable copy behind in this database file or its WAL:
``secure_delete`` zeroes freed space, the full-text index is merged so deleted words leave it, and a
TRUNCATE checkpoint empties the WAL. Counts only are logged, never the text.

The daily Cloud backups are not touched here. Cloud keeps the newest 5 while the agent runs, so a
deleted item leaves the backups after 5 more backups.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from windyfly.memory.database import Database

logger = logging.getLogger(__name__)

PREVIEW_CHARS = 120
MAX_ITEMS = 50

# (kind shown to the owner, table, text columns searched, time column, preview SQL)
_SOURCES: tuple[tuple[str, str, tuple[str, ...], str, str], ...] = (
    ("message", "episodes", ("content", "summary"), "created_at", "role || ': ' || content"),
    ("memory", "nodes", ("name", "metadata"), "updated_at", "type || ': ' || name"),
    ("correction", "failures", ("description", "root_cause", "correction_action"), "created_at", "description"),
    ("goal", "goals", ("text", "closing_note"), "created_at", "text"),
    ("intent", "intents", ("description",), "created_at", "description"),
    ("conflict", "conflicts", ("old_value", "new_value"), "created_at", "new_value"),
    ("tool record", "agent_actions", ("args_json", "error_message"), "created_at", "capability_id"),
    ("event", "events", ("data",), "created_at", "event_type"),
    # Correction skills (language 'playbook') are distilled from the owner's own friction text.
    ("skill", "skills", ("code", "description"), "created_at", "name"),
)
_TABLE = {kind: table for kind, table, *_ in _SOURCES}


@dataclass(frozen=True)
class Item:
    kind: str
    id: str
    when: str
    preview: str


def _terms(words: str) -> list[str]:
    return [t for t in re.split(r"\s+", (words or "").strip()) if t][:8]


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def find(db: Database, words: str, limit: int = MAX_ITEMS + 1) -> list[Item]:
    """Every remembered row that contains all of ``words`` (case-insensitive for ASCII), newest first."""
    terms = _terms(words)
    if not terms:
        return []
    items: list[Item] = []
    for kind, table, cols, when_col, preview_sql in _SOURCES:
        per_term = "(" + " OR ".join(f"COALESCE({c}, '') LIKE ? ESCAPE '\\'" for c in cols) + ")"
        where = " AND ".join([per_term] * len(terms))
        if table == "skills":
            where = f"language = 'playbook' AND {where}"
        params: list[str] = []
        for t in terms:
            params.extend([f"%{_escape_like(t)}%"] * len(cols))
        try:
            rows = db.fetchall(
                f"SELECT id, {when_col} AS w, {preview_sql} AS p FROM {table} WHERE {where} "
                f"ORDER BY {when_col} DESC LIMIT ?",
                (*params, limit),
            )
        except Exception as e:  # a table an old database lacks
            logger.debug("forget: %s not searched (%s)", table, type(e).__name__)
            continue
        for r in rows:
            preview = " ".join(str(r["p"] or "").split())[:PREVIEW_CHARS]
            items.append(Item(kind, str(r["id"]), str(r["w"] or ""), preview))
    items.sort(key=lambda i: i.when, reverse=True)
    return items[:limit]


def forget(db: Database, items: list[Item]) -> dict[str, int]:
    """Delete exactly ``items``; leave no readable copy in this database or its WAL. Returns counts by kind."""
    counts: dict[str, int] = {}
    if not items:
        return counts
    db.execute("PRAGMA secure_delete=ON")
    try:
        for it in items:
            table = _TABLE.get(it.kind)
            if table is None:
                continue
            if table == "nodes":
                db.execute("DELETE FROM edges WHERE source_id = ? OR target_id = ?", (it.id, it.id))
                db.execute("DELETE FROM conflicts WHERE node_id = ?", (it.id,))
            if table == "failures":
                # The correction skill built from this failure carries the owner's words too (Hub, #575).
                row = db.fetchone("SELECT correction_skill_id AS s FROM failures WHERE id = ?", (it.id,))
                if row and row["s"]:
                    cur = db.execute("DELETE FROM skills WHERE id = ?", (row["s"],))
                    if getattr(cur, "rowcount", 0):
                        counts["skill"] = counts.get("skill", 0) + 1
            cur = db.execute(f"DELETE FROM {table} WHERE id = ?", (it.id,))
            if getattr(cur, "rowcount", 0):
                counts[it.kind] = counts.get(it.kind, 0) + 1
        db.commit()
    except Exception:
        db.conn.rollback()
        raise
    if counts.get("message"):
        # External-content FTS keeps a deleted row's words in its index until the segments merge.
        db.execute("INSERT INTO episodes_fts(episodes_fts) VALUES('optimize')")
        db.commit()
    truncated = _truncate_wal(db)
    logger.info("forget: deleted %s; wal_truncated=%s", dict(sorted(counts.items())), truncated)
    return counts


def _truncate_wal(db: Database, tries: int = 5) -> bool:
    """Checkpoint the WAL into the database and truncate it; True when nothing was left behind."""
    for n in range(tries):
        try:
            row = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except Exception as e:
            logger.debug("forget: checkpoint failed (%s)", type(e).__name__)
            return False
        if row is None or row[0] == 0:
            return True
        time.sleep(0.2 * (n + 1))
    return False


# ── Conversation history by date (strand N10.1) ─────────────────────────────────────────────────

# Summaries written FROM conversations: deleting a stretch of history takes these with it.
_DERIVED_NODE_TYPES = ("relationship_moment", "journal_entry", "turnover_letter")


def history_days(db: Database, days: int = 30) -> list[tuple[str, int]]:
    """Messages per UTC day for the last ``days`` days, newest first."""
    rows = db.fetchall(
        "SELECT substr(created_at, 1, 10) AS d, COUNT(*) AS n FROM episodes "
        "WHERE created_at >= datetime('now', ?) GROUP BY d ORDER BY d DESC",
        (f"-{int(days)} days",),
    )
    return [(str(r["d"]), int(r["n"])) for r in rows]


def find_messages(db: Database, words: str, limit: int = MAX_ITEMS + 1) -> list[Item]:
    """Only conversation messages containing all of ``words``."""
    return [i for i in find(db, words, limit=limit) if i.kind == "message"][:limit]


def items_between(db: Database, start: str, end: str) -> list[Item]:
    """Everything a delete of the UTC days ``start``..``end`` (inclusive, YYYY-MM-DD) takes: the
    messages, the summaries made from conversations in those days, and the corrections logged then."""
    lo, hi = f"{start} 00:00:00", f"{end} 23:59:59"
    items: list[Item] = []
    for r in db.fetchall(
        "SELECT id, created_at AS w, role || ': ' || content AS p FROM episodes "
        "WHERE created_at BETWEEN ? AND ? ORDER BY created_at DESC", (lo, hi)):
        items.append(Item("message", str(r["id"]), str(r["w"]), " ".join(str(r["p"]).split())[:PREVIEW_CHARS]))
    marks = ",".join("?" * len(_DERIVED_NODE_TYPES))
    for r in db.fetchall(
        f"SELECT id, created_at AS w, type || ': ' || name AS p FROM nodes "
        f"WHERE type IN ({marks}) AND created_at BETWEEN ? AND ? ORDER BY created_at DESC",
        (*_DERIVED_NODE_TYPES, lo, hi)):
        items.append(Item("memory", str(r["id"]), str(r["w"]), " ".join(str(r["p"]).split())[:PREVIEW_CHARS]))
    for r in db.fetchall(
        "SELECT id, created_at AS w, description AS p FROM failures "
        "WHERE created_at BETWEEN ? AND ? ORDER BY created_at DESC", (lo, hi)):
        items.append(Item("correction", str(r["id"]), str(r["w"]), " ".join(str(r["p"] or "").split())[:PREVIEW_CHARS]))
    return items


def all_history(db: Database) -> list[Item]:
    """Every message, every summary made from conversations, every correction."""
    return items_between(db, "0000-01-01", "9999-12-31")


def parse_days(text: str) -> tuple[str, str] | None:
    """'YYYY-MM-DD' or 'YYYY-MM-DD..YYYY-MM-DD' -> (start, end); None when it is neither."""
    from datetime import date

    parts = [p.strip() for p in (text or "").split("..")]
    if len(parts) not in (1, 2):
        return None
    try:
        days = [date.fromisoformat(p) for p in parts]
    except ValueError:
        return None
    start, end = days[0], days[-1]
    if end < start:
        start, end = end, start
    return start.isoformat(), end.isoformat()
