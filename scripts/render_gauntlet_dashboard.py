#!/usr/bin/env python3
"""Render the launch-gauntlet dashboard from RESULTS.json.

Phase 6.1 of the launch gauntlet — read
~/.windy-stress/LAUNCH_GAUNTLET_RESULTS.json and produce
~/.windy-stress/LAUNCH_GAUNTLET_DASHBOARD.md, a human-scannable
status board with one line per sub-phase. The same renderer powers
the future `/launch-readiness` Telegram command (Phase 6.3).

Status emoji map:
  ✅ green       — passing
  ⏳ in_progress — actively running
  🚫 blocked     — needs human input or upstream phase
  📦 backlog     — deferred, not blocking gauntlet
  ⚪ not_started — leftmost-red candidate
  ❌ red         — known-failing, needs fix
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HOME = Path.home()
RESULTS = HOME / ".windy-stress" / "LAUNCH_GAUNTLET_RESULTS.json"
OUT = HOME / ".windy-stress" / "LAUNCH_GAUNTLET_DASHBOARD.md"

STATUS_EMOJI = {
    "green": "✅",
    "in_progress": "⏳",
    "blocked": "🚫",
    "backlog": "📦",
    "not_started": "⚪",
    "red": "❌",
}


def status_glyph(status: str) -> str:
    return STATUS_EMOJI.get(status, "❓")


def render(results: dict[str, Any]) -> str:
    lines: list[str] = []

    # Header
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append("# WINDY FLY LAUNCH GAUNTLET")
    lines.append(f"_Generated {now} from `LAUNCH_GAUNTLET_RESULTS.json`._")
    lines.append("")

    # Snapshot
    snap = results.get("snapshot", {})
    if snap:
        lines.append("## Snapshot")
        for k, v in sorted(snap.items()):
            lines.append(f"- **{k}**: `{v}`")
        lines.append("")

    # Phases
    lines.append("## Phases")
    lines.append("")
    phases = results.get("phases", [])

    # Roll-up tally
    sub_counts: dict[str, int] = {}
    total_subs = 0
    leftmost_red: str | None = None

    for phase in phases:
        pid = phase.get("id", "?")
        pname = phase.get("name", "<unnamed>")
        pstatus = phase.get("status", "not_started")
        glyph = status_glyph(pstatus)
        lines.append(f"### Phase {pid}: {pname}  {glyph}")
        subs = phase.get("subs", []) or []
        if not subs:
            lines.append("_(no sub-tasks)_")
        else:
            for sub in subs:
                sid = sub.get("id", "?")
                sname = sub.get("name", "<unnamed>")
                sstatus = sub.get("status", "not_started")
                sglyph = status_glyph(sstatus)
                last = sub.get("last_run") or "—"
                lines.append(f"- {sglyph} **{sid}** {sname} _(last: {last})_")
                ev = sub.get("evidence")
                if ev:
                    # Trim long evidence to keep dashboard readable
                    ev_short = ev if len(ev) <= 240 else ev[:237] + "..."
                    lines.append(f"    > {ev_short}")
                sub_counts[sstatus] = sub_counts.get(sstatus, 0) + 1
                total_subs += 1
                # First not-green, non-blocked, non-backlog is the
                # leftmost red.
                if (leftmost_red is None
                        and sstatus in ("not_started", "in_progress", "red")):
                    leftmost_red = f"Phase {pid} / {sid} — {sname}"
        lines.append("")

    # Roll-up + budget
    lines.append("## Roll-up")
    lines.append("")
    for status in ("green", "in_progress", "blocked", "backlog",
                   "not_started", "red"):
        c = sub_counts.get(status, 0)
        if c:
            pct = (c / total_subs * 100) if total_subs else 0
            lines.append(
                f"- {status_glyph(status)} {status}: **{c}/{total_subs}** "
                f"({pct:.0f}%)"
            )
    lines.append("")

    if leftmost_red:
        lines.append(f"**Leftmost red:** {leftmost_red}")
    else:
        green = sub_counts.get("green", 0)
        all_others_resolved = all(
            sub_counts.get(s, 0) == 0
            for s in ("not_started", "in_progress", "red")
        )
        if all_others_resolved and green == total_subs:
            lines.append("**🎉 GAUNTLET FULLY GREEN — READY FOR CANARY 🎉**")
        else:
            lines.append("**No leftmost red (everything is blocked/backlog).**")
    lines.append("")

    # Budget
    budget_today = results.get("budget_today_usd", 0.0)
    budget_cap = results.get("budget_cap_usd", 5.0)
    pct_burn = (budget_today / budget_cap * 100) if budget_cap else 0.0
    bar_full = int(pct_burn / 5)
    bar = "█" * bar_full + "░" * (20 - bar_full)
    lines.append("## Budget")
    lines.append(
        f"`{bar}` ${budget_today:.2f} / ${budget_cap:.2f} "
        f"({pct_burn:.0f}%)"
    )
    lines.append("")

    # Recent iteration log
    log = results.get("iteration_log", []) or []
    if log:
        lines.append("## Recent iterations")
        for entry in log[-10:]:
            ts = entry.get("ts", "?")
            ph = entry.get("phase", "?")
            action = entry.get("action", "?")
            result = entry.get("result", "")
            lines.append(f"- `{ts}` Phase {ph}: {action} → {result}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results",
        type=Path,
        default=RESULTS,
        help=f"Path to RESULTS.json (default: {RESULTS})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=OUT,
        help=f"Path to dashboard.md output (default: {OUT})",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print to stdout instead of writing the file",
    )
    args = parser.parse_args()

    if not args.results.exists():
        print(f"ERROR: {args.results} not found", file=sys.stderr)
        return 1

    with args.results.open() as f:
        results = json.load(f)

    rendered = render(results)

    if args.stdout:
        print(rendered)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered)
        print(f"Rendered → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
