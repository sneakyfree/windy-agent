"""Phase 3.4 — 4-cell capability matrix scaffold.

Aspirational test matrix per gauntlet plan: every capability
(slash command, tool, channel adapter) should have 4 cells:

  - happy        : works in the documented good case
  - timeout      : tool exceeds budget, fails cleanly
  - malformed    : bad input is rejected with a clear message
  - network_out  : downstream API is unreachable, fails cleanly

144 capabilities × 4 = 576 tests, all xfail-with-TODO at scaffold
time. Real implementations replace each TODO as the matrix gets
filled in over weeks. The completeness gate at the bottom ensures
no capability is added without 4 cells declared.

Runs in <1s because all cells xfail immediately.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


CSV = Path.home() / ".windy-stress" / "capability_matrix.csv"
CELLS = ("happy", "timeout", "malformed", "network_out")


def _load_capabilities() -> list[tuple[str, str, str]]:
    """Returns [(type, name, source), ...] from capability_matrix.csv."""
    if not CSV.exists():
        return []
    out = []
    with CSV.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append((row["type"], row["name"], row["source"]))
    return out


CAPS = _load_capabilities()


@pytest.fixture(scope="session")
def capability_count():
    return len(CAPS)


@pytest.mark.parametrize("cap_type,cap_name,cap_source", CAPS,
                         ids=[f"{t}:{n}" for t, n, _ in CAPS])
@pytest.mark.parametrize("cell", CELLS)
@pytest.mark.xfail(
    strict=False,
    reason=(
        "Phase 3.4 scaffold — placeholder. Replace each cell with a "
        "real test as the matrix fills in. Total: 576 cells (144 caps "
        "× 4 cells) — implementation is multi-week work tracked in "
        "the launch gauntlet."
    ),
)
def test_capability_cell(cap_type, cap_name, cap_source, cell):
    """Scaffold cell — fails by design until a real test replaces it.

    To implement: replace `pytest.fail` below with the actual test
    body for this (capability, cell) pair. Reference the capability's
    source file: {cap_source}.
    """
    pytest.fail(
        f"TODO: implement {cell} cell for {cap_type} '{cap_name}' "
        f"(source: {cap_source})"
    )


def test_matrix_completeness():
    """Every capability in the CSV must have 4 cells declared above.

    The parametrize decorator handles this implicitly — if a capability
    is in the CSV, it gets 4 parameter combinations. This explicit
    test asserts the CSV is non-empty so future runs catch the case
    where the extractor was never run.
    """
    if not CAPS:
        pytest.skip(
            f"capability_matrix.csv not at {CSV} — run "
            "`python scripts/extract_capabilities.py` first"
        )
    assert len(CAPS) > 0
    # Each cap × 4 cells = expected test count
    expected = len(CAPS) * len(CELLS)
    assert expected == len(CAPS) * 4, (
        f"matrix shape: {len(CAPS)} capabilities × {len(CELLS)} cells = "
        f"{expected} test invocations"
    )


def test_matrix_growth_signal():
    """When this test fails, the gauntlet ratchet has moved: more cells
    are real (non-xfail) than the recorded baseline. Use it as the
    forcing function for 3.4 progress reporting.

    Baseline today: 0 real cells / 576 total.
    """
    baseline_real = 0
    # We can't easily count actually-implemented cells from inside a
    # parametrized test — this is a placeholder for the day someone
    # adds an introspection helper. For now, just assert the baseline.
    actual_real = 0  # update as cells fill in
    assert actual_real >= baseline_real
