"""Trust — integrity band, clearance level, and action gating.

Eternitas is the source of truth. This package reads trust state via
GET /v1/trust/{passport}, caches it locally for 5 minutes and gates
sensitive actions. The agent PULLS; it has no inbound port, so it takes
no trust.changed push (lean Fly R1).
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
