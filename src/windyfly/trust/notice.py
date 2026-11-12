"""The owner is told, once, when Eternitas lowers the agent's standing (LEGAL DECISION 53).

The agent PULLS its standing from Eternitas (trust/check.py). When a fetched verdict is worse than the one before,
the owner gets one plain mail, by the same owner mail as the other alerts (agent/owner_mail.py). The words are Windy
Legal's, word for word (BOARD 2026-11-11T06:41:02Z, closed 07:03:12Z); the rule for WHEN "lowered" is said is
Eternitas's (BOARD 2026-11-11T07:00:14Z):

- band order, worst to best: critical, poor, fair, good, exceptional; "unproven" is not a penalty and counts as fair;
- mail when the new band is below the old one on that order, or is poor or critical; unproven to fair or back is
  never "lowered";
- suspended and revoked each have their own sentence; an improvement is never mailed.

"Once per change" holds across restarts and across two processes of one agent: the last state seen is a row in the
agent's database, and one atomic UPDATE decides which process owns a change. A mail that could not be sent stays
pending and is tried again at a later fetch, at most once every ten minutes. Nothing here blocks a trust check: the
mail goes out on a helper thread.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Windy Legal, DECISION 53, word for word. {old}/{new} are the band names Eternitas itself shows people.
LOWERED = ("Eternitas has lowered your agent's standing from {old} to {new}. Some things it could do before may now "
           "be refused.")
SUSPENDED = ("Eternitas has suspended your agent's passport. Until that is lifted, things that need it will be "
             "refused.")
REVOKED = ("Eternitas has revoked your agent's passport. Things that need it will be refused. You can appeal at "
           "eternitas.ai.")

SUBJECTS = {
    "lowered": "Eternitas lowered your agent's standing",
    "suspended": "Eternitas suspended your agent's passport",
    "revoked": "Eternitas revoked your agent's passport",
}

# Eternitas's published integrity-bands.v1, worst to best; unproven ranks with fair.
BAND_RANK = {"critical": 0, "poor": 1, "fair": 2, "unproven": 2, "good": 3, "exceptional": 4}

RETRY_AFTER_S = 600

_DDL = """
CREATE TABLE IF NOT EXISTS trust_seen (
    passport TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    band TEXT NOT NULL,
    changed_at REAL NOT NULL,
    notice_kind TEXT,
    notice_old TEXT,
    notice_new TEXT,
    tried_at REAL,
    told_at REAL
);
"""


def notice_for(old_status: str, old_band: str, new_status: str, new_band: str) -> str | None:
    """Which notice a change from (old) to (new) calls for: "revoked", "suspended", "lowered", or None."""
    if new_status == "revoked":
        return "revoked" if old_status != "revoked" else None
    if new_status == "suspended":
        return "suspended" if old_status != "suspended" else None
    if new_status != "active" or old_status != "active" or new_band == old_band:
        return None
    old_rank, new_rank = BAND_RANK.get(old_band), BAND_RANK.get(new_band)
    if old_rank is None or new_rank is None:
        return None      # a name Eternitas does not publish: "lowered from X to Y" could not be said truthfully
    return "lowered" if new_rank < old_rank else None


def text_for(kind: str, old_band: str = "", new_band: str = "") -> str:
    if kind == "revoked":
        return REVOKED
    if kind == "suspended":
        return SUSPENDED
    return LOWERED.format(old=old_band, new=new_band)


def _ensure(db: Any) -> None:
    db.conn.execute(_DDL)
    db.conn.commit()


def observe(passport: str, status: str, band: str, db: Any, *, before: tuple[str, str] | None = None) -> str | None:
    """Record the state just fetched. Returns the notice kind when THIS call saw the state get worse, else None.

    ``before`` is the state the trust cache held before this fetch; it seeds the row the first time, so an agent
    that already had a verdict when this code arrived compares against it. With no state before, the first one seen
    is only recorded: nothing changed that this agent witnessed.
    """
    _ensure(db)
    now = time.time()
    seed_status, seed_band = before if before is not None else (status, band)
    db.conn.execute("INSERT OR IGNORE INTO trust_seen (passport, status, band, changed_at) VALUES (?, ?, ?, ?)",
                    (passport, seed_status, seed_band, now))
    row = db.conn.execute("SELECT status, band FROM trust_seen WHERE passport = ?", (passport,)).fetchone()
    old_status, old_band = row["status"], row["band"]
    if (old_status, old_band) == (status, band):
        db.conn.commit()
        return None
    kind = notice_for(old_status, old_band, status, band)
    # ONE statement decides who owns this change: a second process that fetched at the same moment updates no row.
    # A change with no notice (an improvement, a sideways move) also clears a notice still pending: it would no
    # longer be true.
    cur = db.conn.execute(
        "UPDATE trust_seen SET status = ?, band = ?, changed_at = ?, notice_kind = ?, notice_old = ?, notice_new = ?, "
        "tried_at = NULL WHERE passport = ? AND status = ? AND band = ?",
        (status, band, now, kind, old_band if kind else None, band if kind else None, passport, old_status, old_band))
    db.conn.commit()
    if cur.rowcount != 1:
        return None
    if kind:
        logger.warning("Trust: Eternitas changed this agent's standing for the worse (%s); the owner is told once",
                       kind)
    return kind


def deliver_pending(passport: str, db: Any, *, now: float | None = None) -> bool:
    """Send the pending notice, if there is one and nobody tried in the last ten minutes. True when it was sent."""
    _ensure(db)
    now = time.time() if now is None else now
    cur = db.conn.execute(
        "UPDATE trust_seen SET tried_at = ? WHERE passport = ? AND notice_kind IS NOT NULL "
        "AND (tried_at IS NULL OR tried_at < ?)", (now, passport, now - RETRY_AFTER_S))
    db.conn.commit()
    if cur.rowcount != 1:
        return False
    row = db.conn.execute("SELECT notice_kind, notice_old, notice_new FROM trust_seen WHERE passport = ?",
                          (passport,)).fetchone()
    if not row or not row["notice_kind"]:
        return False
    kind = row["notice_kind"]
    from windyfly.agent import owner_mail

    sent = owner_mail.send_sync(SUBJECTS[kind], text_for(kind, row["notice_old"] or "", row["notice_new"] or ""))
    if not sent:
        logger.warning("Trust: the owner could not be told about the %s standing yet; trying again later", kind)
        return False
    # Cleared only if it is still the notice that was sent (a newer change may have replaced it meanwhile).
    db.conn.execute("UPDATE trust_seen SET notice_kind = NULL, notice_old = NULL, notice_new = NULL, told_at = ? "
                    "WHERE passport = ? AND notice_kind = ? AND tried_at = ?", (time.time(), passport, kind, now))
    db.conn.commit()
    return True


def has_pending(passport: str, db: Any) -> bool:
    _ensure(db)
    row = db.conn.execute("SELECT notice_kind FROM trust_seen WHERE passport = ?", (passport,)).fetchone()
    return bool(row and row["notice_kind"])


def deliver_in_background(passport: str) -> threading.Thread:
    """Never on the caller's thread or loop: a trust check must not wait for a mail."""
    def run() -> None:
        try:
            from windyfly.trust.check import _db

            deliver_pending(passport, _db())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Trust: owner notice failed (%s)", type(exc).__name__)

    t = threading.Thread(target=run, name="trust-owner-notice", daemon=True)
    t.start()
    return t
