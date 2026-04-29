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
)


# Verdict ordering for "got worse" detection.
_RANK = {"green": 0, "yellow": 1, "red": 2}


def _changed_for_worse(prev: dict, cur: dict) -> list[dict]:
    """Return organs whose verdict got worse since prev."""
    prev_o = (prev or {}).get("organs") or {}
    cur_o = (cur or {}).get("organs") or {}
    out = []
    for organ, cur_data in cur_o.items():
        cur_v = (cur_data or {}).get("verdict")
        prev_v = (prev_o.get(organ) or {}).get("verdict", "green")
        if not cur_v:
            continue
        if _RANK.get(cur_v, 0) > _RANK.get(prev_v, 0):
            out.append({
                "organ": organ,
                "previous": prev_v,
                "current": cur_v,
            })
    return out


def main() -> int:
    snaps = _load_snapshots(limit=2)
    if not snaps:
        # Nothing to report; first run not yet completed.
        return 0
    cur = snaps[-1]
    prev = snaps[-2] if len(snaps) >= 2 else {}

    cur_organs = cur.get("organs") or {}
    cur_red = [o for o, d in cur_organs.items() if (d or {}).get("verdict") == "red"]
    transitions = _changed_for_worse(prev, cur)

    # The alarm fires when EITHER:
    #   - any organ is currently red (regardless of trend), or
    #   - any organ got strictly worse since the previous snapshot
    if not cur_red and not transitions:
        return 0  # silent — no alarm

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
