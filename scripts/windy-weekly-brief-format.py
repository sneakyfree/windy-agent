"""Format the weekly self-assessment brief from health snapshots.

Used by ``windy-weekly-brief.sh`` — kept as a separate file so bash
command-substitution + heredoc + Python apostrophes don't fight each
other (lesson learned 2026-04-29).

Reads ``$WINDY_HEALTH_DIR`` for v10 organ-harmony scorecards, calls
the same diagnosis logic the in-bot ``health.weekly_brief``
capability uses, prints a Telegram-ready Markdown brief to stdout.
"""

from __future__ import annotations

import os
import sys

# Allow running before windyfly is pip-installed by adding the repo
# src/ to the path.
_AGENT_SRC = os.environ.get("_AGENT_SRC")
if _AGENT_SRC and os.path.isdir(_AGENT_SRC):
    sys.path.insert(0, _AGENT_SRC)

from windyfly.agent.capabilities.health import (
    _build_recommendations,
    _load_snapshots,
    _summarize_latest,
)


def main() -> int:
    snaps = _load_snapshots(limit=10)
    recs = _build_recommendations(snaps)

    lines: list[str] = ["🔬 *Weekly Self-Assessment*"]

    if snaps:
        latest = _summarize_latest(snaps[-1])
        ts = (latest.get("ts") or "?").replace("T", " ")[:16]
        model = latest.get("model") or "?"
        lines.append(f"{ts} — model: {model}")
        lines.append("")

        counts = latest.get("verdict_counts") or {}
        green = counts.get("green", 0)
        yellow = counts.get("yellow", 0)
        red = counts.get("red", 0)
        if red:
            lines.append(f"⚠️ *{red} organ(s) RED* — attention needed")
        elif yellow:
            lines.append(f"🟡 *{yellow} organ(s) yellow* — tuning suggested")
        else:
            lines.append(f"✅ *All {green} organs healthy*")
        lines.append("")

        lines.append("*Organ status:*")
        glyph = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
        for organ, data in (latest.get("organs") or {}).items():
            v = (data or {}).get("verdict", "?")
            d = ((data or {}).get("detail") or "")[:60]
            lines.append(f"{glyph.get(v, '·')} {organ}: {d}")
        lines.append("")
    else:
        lines.append("(No scorecards yet — first run building baseline.)")
        lines.append("")

    real_recs = [r for r in recs if r.get("verdict") != "no_data"]
    if real_recs:
        lines.append("*Recommendations:*")
        for r in real_recs:
            organ = r.get("organ") or "?"
            diag = r.get("diagnosis") or ""
            rec = r.get("recommendation") or ""
            action = r.get("user_action") or ""
            lines.append(f"• *{organ}*: {diag}")
            lines.append(f"  → {rec}")
            if action:
                lines.append(f"  💬 _{action}_")
            if r.get("trend_note"):
                lines.append(f"  📉 {r['trend_note']}")
        lines.append("")
    else:
        lines.append("_No recommendations this week — everything is running smoothly._")
        lines.append("")

    lines.append("Whenever you want a fresh start, just say /reset.")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
