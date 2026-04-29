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
    _ORGAN_FRIENDLY_NAMES,
    _build_recommendations,
    _grandma_detail,
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
        lines.append(f"_Checkup from {ts}_")
        lines.append("")

        counts = latest.get("verdict_counts") or {}
        green = counts.get("green", 0)
        yellow = counts.get("yellow", 0)
        red = counts.get("red", 0)
        # Grandma-first headlines — feeling, not metrics.
        if red:
            lines.append("⚠️ *I'm having a tough time. Could you check on me?*")
        elif yellow:
            lines.append("🟡 *I'm doing OK, but a few things feel off this week.*")
        else:
            lines.append("✅ *I've been running smoothly! Nothing to worry about.*")
        lines.append("")

        lines.append("*How I'm feeling:*")
        glyph = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
        for organ, data in (latest.get("organs") or {}).items():
            v = (data or {}).get("verdict", "?")
            raw = ((data or {}).get("detail") or "")
            friendly_name = _ORGAN_FRIENDLY_NAMES.get(organ, organ)
            feeling = _grandma_detail(organ, v, raw)
            lines.append(f"{glyph.get(v, '·')} {friendly_name}: {feeling}")
        lines.append("")
    else:
        lines.append("_(I haven't done a checkup yet — first run is building my baseline.)_")
        lines.append("")

    real_recs = [r for r in recs if r.get("verdict") != "no_data"]
    if real_recs:
        lines.append("*What I think would help:*")
        for r in real_recs:
            diag = r.get("diagnosis") or ""
            rec = r.get("recommendation") or ""
            action = r.get("user_action") or ""
            lines.append(f"• {diag}")
            lines.append(f"  {rec}")
            if action:
                lines.append(f"  👉 *{action}*")
        lines.append("")
    else:
        lines.append("_Nothing needs fixing this week. Just enjoy the bot._")
        lines.append("")

    lines.append("Whenever you want a fresh start, just say /reset.")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
