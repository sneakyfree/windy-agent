"""Epistemic 1-liner prompt section.

Always-emitted instruction nudging the model to surface confidence
when stating memory facts + respect the INFERRED marker on nodes.

Pre-launch addition (no specific PR — present in initial commit).
"""

from __future__ import annotations


EPISTEMIC_TEXT: str = (
    "When you state a fact from memory, indicate your confidence level. "
    "If a fact is marked INFERRED, say so."
)
