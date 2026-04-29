#!/usr/bin/env bash
# Show the agent's organ-health trend across recent v10 runs.
#
# Reads ~/.windy-stress/health/*.json (one scorecard per v10 run)
# and prints:
#   - Current scorecard
#   - Per-organ trend across the last N runs (which dropped/improved?)
#   - Any organ that has degraded since the previous run (regression
#     flag — what to investigate)
#
# Pure read. No mutation. Safe to run anytime.
#
# Usage: bash scripts/windy-health-trend.sh [N]

set -uo pipefail

HEALTH_DIR="${WINDY_HEALTH_DIR:-/home/grantwhitmer/.windy-stress/health}"
N="${1:-10}"

if [[ ! -d "$HEALTH_DIR" ]]; then
    echo "No health snapshots found at $HEALTH_DIR"
    echo "Run stress_v10_organ_harmony.py at least once."
    exit 1
fi

python3 - "$HEALTH_DIR" "$N" <<'PYEOF'
import json
import sys
from pathlib import Path

health_dir = Path(sys.argv[1])
n = int(sys.argv[2])

files = sorted(health_dir.glob("*.json"))
if not files:
    print(f"No snapshot files in {health_dir}")
    sys.exit(1)

snapshots = []
for p in files[-n:]:
    try:
        snapshots.append(json.loads(p.read_text()))
    except Exception as e:
        print(f"  (skip unparseable {p.name}: {e})")

if not snapshots:
    print("No parseable snapshots.")
    sys.exit(1)

print(f"\nLast {len(snapshots)} v10 organ-harmony snapshots:\n")
print(f"  {'when':18} {'mode':6} {'green':>6} {'yellow':>7} {'red':>5}")
print(f"  {'-'*18} {'-'*6} {'-'*6} {'-'*7} {'-'*5}")
for s in snapshots:
    ts = s.get("run_id") or s.get("ts", "?")[:18]
    mode = "haiku" if s.get("real_llm") else "mock"
    counts = s.get("verdict_counts") or {}
    if not counts:
        # legacy format: derive from organs dict
        organs = s.get("organs") or {}
        counts = {
            "green":  sum(1 for v in organs.values() if v.get("verdict") == "green"),
            "yellow": sum(1 for v in organs.values() if v.get("verdict") == "yellow"),
            "red":    sum(1 for v in organs.values() if v.get("verdict") == "red"),
        }
    print(f"  {ts:18} {mode:6} {counts.get('green', 0):>6} "
          f"{counts.get('yellow', 0):>7} {counts.get('red', 0):>5}")

# Per-organ tracking
print("\nPer-organ verdicts (oldest → newest):")
organ_names = list((snapshots[-1].get("organs") or {}).keys())
for organ in organ_names:
    glyphs = []
    for s in snapshots:
        verdict = (s.get("organs", {}).get(organ, {}) or {}).get("verdict", "?")
        glyphs.append({"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(verdict, "·"))
    print(f"  {organ:12} {' '.join(glyphs)}")

# Regression detection — compare last two
if len(snapshots) >= 2:
    prev, cur = snapshots[-2], snapshots[-1]
    prev_o = prev.get("organs", {})
    cur_o = cur.get("organs", {})
    rank = {"green": 0, "yellow": 1, "red": 2}
    regressions = []
    for organ, cur_data in cur_o.items():
        cur_v = cur_data.get("verdict")
        prev_v = (prev_o.get(organ) or {}).get("verdict")
        if cur_v and prev_v and rank.get(cur_v, 0) > rank.get(prev_v, 0):
            regressions.append((organ, prev_v, cur_v))
    if regressions:
        print("\n⚠ REGRESSIONS since previous run:")
        for organ, prev_v, cur_v in regressions:
            print(f"  - {organ}: {prev_v} → {cur_v}")
    else:
        print("\n✓ No organ regression vs previous run.")

print()
PYEOF
