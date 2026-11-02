"""No live-shaped Stripe secret in the PUBLIC repo's tests (Windy Git's public scan, 2026-11-02).

Test fixtures are made-up values, but a string shaped like a live Stripe key ("sk_" + "live_" + body) trips every
secret scanner and looks real to a reader. Fixtures say what they are ("fixture_..."). The pattern below is built
from pieces so this file never contains the shape it forbids."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_SHAPED = re.compile("(?:s|r)k" + "_" + "live" + r"_[A-Za-z0-9]{8,}")


def offenders(text: str) -> int:
    return len(LIVE_SHAPED.findall(text))


def test_no_test_file_carries_a_live_shaped_stripe_key():
    hits = [str(p.relative_to(ROOT)) for p in sorted((ROOT / "tests").rglob("*.py"))
            if offenders(p.read_text(encoding="utf-8", errors="replace"))]
    assert hits == [], f"live-shaped Stripe strings in tests (use a fixture_ value): {hits}"


def test_the_check_sees_the_shape():
    assert offenders("KEY = 'sk" + "_live_" + "51abcdefgh'") == 1
    assert offenders("rk" + "_live_" + "abcdefghij") == 1
    assert offenders("fixture_51AbCdEfGhIjKlMn0123") == 0
    assert offenders("sk" + "_live_" + "short") == 0  # a docstring mention, not a value
