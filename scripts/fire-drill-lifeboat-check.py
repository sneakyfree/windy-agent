#!/usr/bin/env python3
"""Pass/fail check for the fire drill's lifeboat step (scripts/fire-drill.sh).

The lifeboat step asks a scratch agent with a dead Mind "What is 2+2?".
It passes when the reply carries a lifeboat marker AND the answer.

Two markers count, because the agent has two honest ways to say it is on
the local floor:
  - the lifebuoy 🛟 (fallback notice, or a reply already marked 🛟);
  - the auto-resurrect notice: 🚨 plus "free local model" (loop.py
    _auto_resurrect_banner; it has started with 🚨 since #145).

The answer is looked for only AFTER the notice, so a digit in the notice
itself (a model name like phi4) can never pass for the answer.

CLI: reads the reply on stdin, prints one LIFEBOAT_CHECK line, exits 0/1.
"""
from __future__ import annotations

import re
import sys

LIFEBUOY = "🛟"
SIREN = "🚨"
LOCAL_NOTICE = "free local model"
NOTICE_END = "\n---\n"

# "4" on its own ("= 4." counts), never part of a number like 40 or 3.4.
_ANSWER = re.compile(r"(?<![\d.])4(?!\.?\d)|\bfour\b", re.IGNORECASE)


def _split(reply: str) -> tuple[str, str]:
    """(notice, answer part). With no notice separator, a leading marker is the notice."""
    if NOTICE_END in reply:
        notice, _, rest = reply.partition(NOTICE_END)
        return notice, rest
    stripped = reply.lstrip()
    if stripped.startswith(LIFEBUOY):
        return LIFEBUOY, stripped[len(LIFEBUOY):]
    return "", reply


def check(reply: str) -> tuple[bool, str]:
    notice, answer = _split(reply or "")
    lifebuoy = LIFEBUOY in (reply or "")
    siren_notice = SIREN in notice and LOCAL_NOTICE in notice
    if not (lifebuoy or siren_notice):
        return False, "no lifeboat marker"
    if not _ANSWER.search(answer):
        return False, "marker but no answer to 2+2"
    return True, "marker=" + ("lifebuoy" if lifebuoy else "local-model notice") + " + answer"


def main() -> int:
    ok, why = check(sys.stdin.read())
    print(f"LIFEBOAT_CHECK: {'PASS' if ok else 'FAIL'} ({why})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
