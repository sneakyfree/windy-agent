"""canonical.py — deterministic JSON serialization for signing.

Mirrored exactly by packages/sdk/src/lib/canonical.ts:
  recursive lexicographic key sort, compact separators, no whitespace.

Used to derive the bytes signed by `windy-drops sign` so the signed_digest
is reproducible from either SDK (and verifiable by the registry).
"""

from __future__ import annotations

import json
from typing import Any


def canonicalize(value: Any) -> str:
    """Recursive lexicographic-key, compact-separator JSON serialization."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
