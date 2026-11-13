"""Small honesty fixes (Hub 10-12, R4b).

- N2.21: the hatch summary's "Suspended/Revoked" branch read _WINDYFLY_PASSPORT_DEAD, which nothing
  ever set; the dead branch is gone.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_dead_passport_branch_is_gone():
    assert "_WINDYFLY_PASSPORT_DEAD" not in (ROOT / "src" / "windyfly" / "hatching.py").read_text(encoding="utf-8")
