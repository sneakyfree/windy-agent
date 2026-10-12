"""Small honesty fixes (Hub 10-12, R4b).

- P5.93.5: GET /api/journal answers with the same key whether the brain is up ("journal", from the
  bridge's journal.list) or down (the gateway's offline fallback said "entries").
- N2.21: the hatch summary's "Suspended/Revoked" branch read _WINDYFLY_PASSPORT_DEAD, which nothing
  ever set; the dead branch is gone.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_gap_closure import _make_bridge, _run

ROOT = Path(__file__).resolve().parents[1]


def test_the_journal_has_one_key_whether_the_brain_is_up_or_down():
    bridge, db, _ = _make_bridge()
    try:
        up = _run(bridge._dispatch("journal.list", {}))
    finally:
        db.close()
    server = (ROOT / "gateway" / "src" / "server.ts").read_text(encoding="utf-8")
    offline = re.search(r"export const JOURNAL_OFFLINE = \{([^}]*)\}", server)
    assert set(up) == {"journal"}
    assert offline and re.search(r"\bjournal:\s*\[\]", offline.group(1))
    route = server[server.index('path === "/api/journal"'):][:400]
    assert "JOURNAL_OFFLINE" in route and "entries" not in route


def test_the_dead_passport_branch_is_gone():
    assert "_WINDYFLY_PASSPORT_DEAD" not in (ROOT / "src" / "windyfly" / "hatching.py").read_text(encoding="utf-8")
