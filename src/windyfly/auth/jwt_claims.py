"""Read-only JWT claim extraction.

We do NOT verify the signature here — verification is the business of
the service that minted the JWT (windy-pro). This module is for
cases where we want to use a claim (typically `sub`, the Windy
identity id) out of a token we already trust because we got it from
our own login flow.

Used by the hatch orchestrator to derive `windy_identity_id` from
the owner's JWT when `WINDY_IDENTITY_ID` wasn't set explicitly
(P1-E2).
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _b64url_decode_to_json(segment: str) -> dict[str, Any] | None:
    try:
        rem = len(segment) % 4
        if rem:
            segment += "=" * (4 - rem)
        raw = base64.urlsafe_b64decode(segment.encode("ascii"))
        parsed = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.debug("JWT payload decode failed: %s", exc)
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def read_jwt_claims(token: str) -> dict[str, Any] | None:
    """Return the payload claims dict, or None if the token is malformed.

    DOES NOT VERIFY the signature. Use only for claims the caller
    already trusts in context.
    """
    if not token:
        return None
    token = token.strip()
    if token.startswith("Bearer "):
        token = token[len("Bearer "):].strip()
    parts = token.split(".")
    if len(parts) != 3:
        return None
    return _b64url_decode_to_json(parts[1])


def identity_from_jwt(token: str) -> str:
    """Extract `windy_identity_id` (preferred) or `sub` from the JWT.

    Returns "" when the token is missing or malformed. Used as a
    fallback for link-back when `WINDY_IDENTITY_ID` wasn't set
    explicitly.
    """
    claims = read_jwt_claims(token)
    if not claims:
        return ""
    for key in ("windy_identity_id", "sub"):
        val = claims.get(key)
        if isinstance(val, str) and val:
            return val
    return ""
