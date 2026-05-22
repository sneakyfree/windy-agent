#!/usr/bin/env python3
"""Phase 7.2 — auto-classify Windy Fly log events into P1/P2/P3.

Reads ~/.windy/windy-0.log (or stdin), pattern-matches against the
severity definitions in docs/ESCALATION.md, prints a tally + the
top-N matches per tier.

Usage:
  python scripts/canary-classify.py                # last 24h
  python scripts/canary-classify.py --hours 168    # last 7d
  python scripts/canary-classify.py --json         # JSON summary
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

LOG = Path.home() / ".windy" / "windy-0.log"


# (severity, name, regex-pattern)
PATTERNS = [
    # P1 — wake Grant
    ("P1", "auth_perma_dead",
     r"is_permanent_auth_error|permanent_auth_failure"),
    ("P1", "service_failed",
     r"systemd.*windy-0.*failed|main_loop.*EXIT.*\d"),
    ("P1", "lifeboat_wedged",
     r"lifeboat wedged"),
    ("P1", "oom_kill",
     r"out of memory|MemoryError"),
    ("P1", "disk_full",
     r"No space left on device|ENOSPC"),
    ("P1", "db_locked_extended",
     r"database is locked.*retry.*5"),

    # P2 — morning digest
    ("P2", "auth_401_transient",
     r"HTTP.*401.*invalid x-api-key"),
    ("P2", "anthropic_5xx",
     r"HTTP.*5\d{2}.*api\.anthropic"),
    ("P2", "rate_limit",
     r"HTTP.*429"),
    ("P2", "tool_timeout",
     r"capability.*timeout|tool.*timed out"),
    ("P2", "channel_start_failure",
     r"primary channel start failed"),

    # P3 — log only
    ("P3", "test_flake",
     r"FLAKY|flake detected"),
    ("P3", "cosmetic_warning",
     r"DeprecationWarning|UserWarning"),
    ("P3", "slow_turn",
     r"elapsed.*[1-5][0-9]s|turn took"),
]


def _read_recent(path: Path, hours: int) -> list[str]:
    if not path.exists():
        return []
    # Log lines start with "HH:MM:SS" so true cutoff-by-timestamp
    # would require parsing each line. Best-effort: keep last
    # hours*1500 lines (~1.5kB/line average × hours).
    lines = path.read_text(errors="replace").splitlines()
    approx_lines = hours * 1500
    return lines[-approx_lines:] if len(lines) > approx_lines else lines


def _classify(lines: list[str]) -> dict[str, Any]:
    tally: dict[str, int] = {"P1": 0, "P2": 0, "P3": 0}
    by_pattern: dict[str, dict[str, Any]] = {}
    examples: dict[str, list[str]] = {"P1": [], "P2": [], "P3": []}

    for line in lines:
        for severity, name, pattern in PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                tally[severity] += 1
                key = f"{severity}/{name}"
                if key not in by_pattern:
                    by_pattern[key] = {
                        "severity": severity,
                        "name": name,
                        "pattern": pattern,
                        "count": 0,
                    }
                by_pattern[key]["count"] += 1
                if len(examples[severity]) < 3:
                    examples[severity].append(line[:200])
                break  # one classification per line

    return {
        "tally": tally,
        "patterns": list(by_pattern.values()),
        "examples": examples,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=24,
                    help="Look-back window in hours (default 24)")
    ap.add_argument("--json", action="store_true",
                    help="Output JSON summary")
    ap.add_argument("--log", type=Path, default=LOG,
                    help=f"Path to bot log (default {LOG})")
    args = ap.parse_args()

    lines = _read_recent(args.log, args.hours)
    if not lines:
        print(f"ERROR: no log lines found at {args.log}", file=sys.stderr)
        return 1

    result = _classify(lines)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    t = result["tally"]
    print(f"Windy Fly canary triage — last {args.hours}h")
    print("-" * 60)
    print(f"  P1 (wake Grant): {t['P1']}")
    print(f"  P2 (morning):    {t['P2']}")
    print(f"  P3 (cosmetic):   {t['P3']}")
    print()
    if result["patterns"]:
        print("Top patterns:")
        for p in sorted(result["patterns"],
                        key=lambda x: (x["severity"], -x["count"])):
            print(f"  [{p['severity']}] {p['name']:<28} {p['count']:>4} hits")
    if t["P1"]:
        print("\nP1 examples:")
        for ex in result["examples"]["P1"]:
            print(f"  {ex[:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
