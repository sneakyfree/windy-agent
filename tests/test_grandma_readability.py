"""Phase 8.4 — block new grandma-jargon in user-facing strings.

Stub gate: scans the same string set the v8.1 extractor finds for
known jargon terms that a grandma wouldn't understand. Override list
keeps the false-positives small (terms that ARE OK in context).

When this test fails, either:
  - rewrite the string in plain English, OR
  - add the new term to ``OVERRIDE_TERMS`` with a justification
    comment explaining why grandma-readers tolerate it here.

This is intentionally a low-friction gate; the full grandma-rewriter
pass (Phase 8.2) is the heavier check that runs out-of-band.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
EXTRACTOR = REPO / "scripts" / "extract_user_strings.py"
USER_STRINGS = Path.home() / ".windy-stress" / "user_strings.txt"


# Jargon terms grandma won't recognize. Case-insensitive substring.
JARGON_TERMS = (
    "context window",   # use "working memory" — gauntlet Phase 1.LOW_MEM
    "API key",          # use "credential" or "login"
    "stdout",
    "stderr",
    "fd_count",
    "epoch",
    "UUID",
    "fingerprint",      # ok in context but flag for review
    "OAuth",
    "JWT",
    "tokens",           # use "words" or "memory used"
    "ratelimit",
)

# Terms that LOOK like jargon but are fine in the specific contexts
# they appear. Each entry: (jargon-term, substring-that-makes-it-OK).
# If the user-string contains the OK substring, the jargon is allowed.
OVERRIDE_CONTEXTS = (
    ("API key", "your API key looks invalid"),   # PR #209 dedicated reply IS the right message
    ("API key", "refresh the credential"),       # same context
    ("fingerprint", "fingerprint:"),              # operator-mode label in /status
    ("tokens", "input_tokens"),                   # in JSON schema docs
    ("tokens", "output_tokens"),
    ("tokens", "max_tokens"),
)


def _is_overridden(string: str, term: str) -> bool:
    for ot, ctx in OVERRIDE_CONTEXTS:
        if ot.lower() == term.lower() and ctx.lower() in string.lower():
            return True
    return False


@pytest.mark.skipif(
    not EXTRACTOR.exists(),
    reason=f"extractor not on this branch: {EXTRACTOR}",
)
def test_no_new_grandma_jargon() -> None:
    """Re-run the extractor, then assert no jargon-bearing user-string
    appears uncovered by the OVERRIDE list.
    """
    # Re-extract — fresh signal each run
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"extractor crashed: {result.stderr[-500:]}"
    )

    if not USER_STRINGS.exists():
        pytest.skip(f"no user_strings.txt at {USER_STRINGS}")
    raw = USER_STRINGS.read_text().splitlines()

    violations: list[str] = []
    for line in raw:
        if not line or line.startswith("#"):
            continue
        # Format: <file>:<line>: <string>
        m = re.match(r"^([^:]+):(\d+):\s+(.*)$", line)
        if not m:
            continue
        path, lineno, s = m.groups()
        for term in JARGON_TERMS:
            if term.lower() in s.lower() and not _is_overridden(s, term):
                violations.append(f"  {path}:{lineno}: contains '{term}' — {s[:80]}")
                break  # one violation per string is enough

    if violations:
        # Limit reported violations so test output stays readable
        head = violations[:15]
        more = (
            f"\n  ... and {len(violations) - 15} more"
            if len(violations) > 15 else ""
        )
        pytest.fail(
            f"{len(violations)} user-facing string(s) contain jargon a "
            f"grandma wouldn't recognize. Rewrite in plain English, "
            f"OR add to OVERRIDE_CONTEXTS in this file with reason.\n"
            + "\n".join(head)
            + more
        )
