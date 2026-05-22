"""Regression guard for silent-fail wiring bugs in slash commands.

The v19 audit (`~/.windy-stress/stress_v19_slash_audit.py`) exhaustively
invokes every registered slash command through the unified dispatcher
and categorizes each as GREEN / YELLOW / RED / SKIPPED. RED indicates a
real wiring bug (wrong import, wrong arg order, missing required arg);
PRs #214 (sliders/slider/preset) and #215 (budget/tokens/intents/
failures/decay/benchmark) both surfaced through this audit. Wiring it
as a pytest closes the loop so the next such bug is caught on the PR
that introduces it instead of on Grant's next manual /sliders attempt.

The harness lives outside the repo because Grant also runs it
standalone for QA; this wrapper just enforces the contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path.home() / ".windy-stress" / "stress_v19_slash_audit.py"
LOG_DIR = Path.home() / ".windy-stress" / "logs"


@pytest.mark.skipif(
    not HARNESS.exists(),
    reason=f"v19 harness not installed at {HARNESS}",
)
def test_v19_slash_audit_no_red() -> None:
    """No registered slash command may surface as RED.

    RED markers (from the harness): "unknown command", "cannot import
    name", "unexpected keyword argument", "missing N required positional
    argument", "attributeerror", "no attribute", "modulenotfounderror".

    All of these indicate a handler was registered but never actually
    wired — exactly the bug class PR #214/#215 hunted down.
    """
    result = subprocess.run(
        [sys.executable, str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "v19 harness crashed:\n"
        f"  stdout (last 1000): {result.stdout[-1000:]}\n"
        f"  stderr (last 1000): {result.stderr[-1000:]}"
    )

    summaries = sorted(LOG_DIR.glob("v19_slash_audit_*.summary.json"))
    assert summaries, (
        f"v19 harness produced no summary in {LOG_DIR}; "
        f"stdout was: {result.stdout[-500:]}"
    )
    with summaries[-1].open() as f:
        summary = json.load(f)

    reds = [
        r for r in summary.get("results", [])
        if r.get("category") == "RED"
    ]
    if reds:
        detail = "\n".join(
            f"  - /{r['command']}: "
            f"{(r.get('reply_preview') or r.get('exception') or '')[:160]}"
            for r in reds
        )
        pytest.fail(
            f"v19 audit found {len(reds)} silently-broken slash handlers:\n"
            f"{detail}\n"
            f"This is the PR #214/#215 bug class — fix the wiring "
            f"(import name, arg order, signature) before merging."
        )
