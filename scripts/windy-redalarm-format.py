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
    build_alarm_text,
    _load_snapshots,
)


def main() -> int:
    text = build_alarm_text(_load_snapshots(limit=2))
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
