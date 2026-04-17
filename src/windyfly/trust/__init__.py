"""Trust — integrity band, clearance level, and action gating.

Eternitas is the source of truth. This package reads trust state via
GET /v1/trust/{passport}, caches it locally for 5 minutes, gates
sensitive actions, and reacts to trust.changed webhooks.
"""

from windyfly.trust.check import (
    TrustDecision,
    TrustSnapshot,
    check_trust,
    get_trust,
    invalidate_trust_cache,
)
from windyfly.trust.gate import TrustDenied, require_trust

__all__ = [
    "TrustDecision",
    "TrustDenied",
    "TrustSnapshot",
    "check_trust",
    "get_trust",
    "invalidate_trust_cache",
    "require_trust",
]
