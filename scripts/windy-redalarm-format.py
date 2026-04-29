"""Format a mid-week red-alarm notification.

Used by ``windy-redalarm.sh``. Reads the two most recent scorecards
and produces:

  - Empty string if no degradation since previous → don't DM
  - Short urgent Markdown message if any RED organ OR any
    yellow→red transition since previous

Kept separate from the Sunday weekly brief because the design goal
is different:
  - Sunday brief: warm, full report, sent every week
  - Red alarm: short, urgent, ONLY when the user needs to know

The alarm uses the same grandma-friendly language (no jargon, no
metrics) but compresses to 4-6 lines maximum so it reads as an
alert rather than a checkup.

Outputs an EMPTY string when no alarm is warranted — caller checks
length to decide whether to deliver.
"""

from __future__ import annotations

import os
import sys

_AGENT_SRC = os.environ.get("_AGENT_SRC")
if _AGENT_SRC and os.path.isdir(_AGENT_SRC):
    sys.path.insert(0, _AGENT_SRC)

from windyfly.agent.capabilities.health import (
    _ORGAN_FRIENDLY_NAMES,
    _ORGAN_RECOMMENDATIONS,
    _grandma_detail,
    _load_snapshots,
    should_fire_alarm,
)


def main() -> int:
    snaps = _load_snapshots(limit=2)
    decision = should_fire_alarm(snaps)
    if not decision.get("fire"):
        return 0  # silent — no alarm

    cur_red = decision["current_red"]
    transitions = decision["transitions"]
    cur = snaps[-1]

    lines: list[str] = []
    if cur_red:
        lines.append("🚨 *Heads up — I need attention*")
    else:
        lines.append("⚠️ *Heads up — something feels off*")
    lines.append("")

    # Most-urgent first: RED organs, then transitions.
    reported_organs: set[str] = set()
    for organ in cur_red:
        if organ in reported_organs:
            continue
        reported_organs.add(organ)
        friendly = _ORGAN_FRIENDLY_NAMES.get(organ, organ)
        feeling = _grandma_detail(organ, "red", "")
        rec = (_ORGAN_RECOMMENDATIONS.get(organ, {}).get("red") or {})
        action = rec.get("user_action") or "Say /reset."
        lines.append(f"🔴 *{friendly}*: {feeling}.")
        lines.append(f"  👉 *{action}*")

    for t in transitions:
        organ = t["organ"]
        if organ in reported_organs:
            continue
        reported_organs.add(organ)
        friendly = _ORGAN_FRIENDLY_NAMES.get(organ, organ)
        cur_v = t["current"]
        feeling = _grandma_detail(organ, cur_v, "")
        glyph = {"yellow": "🟡", "red": "🔴"}.get(cur_v, "·")
        rec = (_ORGAN_RECOMMENDATIONS.get(organ, {}).get(cur_v) or {})
        action = rec.get("user_action") or "Say /reset."
        lines.append(f"{glyph} *{friendly}*: {feeling}.")
        lines.append(f"  👉 *{action}*")

    lines.append("")
    lines.append("_Your memory and personality stay safe across /reset._")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
