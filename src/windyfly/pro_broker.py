"""Windy Pro managed-credential broker client.

Used by ``windy go`` to skip the "paste your API key" prompt when the
user already has a Windy Pro account. The flow:

1. Look for ``~/.windypro/config.json``. If missing → user has no Pro
   account on this machine; fall through to the paste-a-key flow.
2. Read the Pro account token (identity reference) from the config.
   If missing → fall through.
3. POST to ``<pro_base>/api/v1/agent/credentials/issue`` signed with
   HMAC-SHA256 over the raw request body, keyed by
   ``WINDY_BROKER_SIGNING_SECRET`` (a secret shared between this
   machine and the Pro instance it's paired with). The signature rides
   in ``X-Windy-Signature: sha256=<hex>`` — same header layout as the
   trust webhook verifier.

   Request body::

       {
         "windy_identity_id": "wi_...",
         "passport_number":   "ET26-...",
         "scope":             "hatch",
         "duration_seconds":  86400
       }

   Response body::

       {
         "broker_token":     "wk_broker_...",
         "provider":         "anthropic",
         "model":            "claude-3-5-sonnet-latest",
         "expires_at":       "2026-04-19T14:32:07Z",
         "usage_cap_tokens": 1_000_000
       }

4. Return a ``BrokeredCredential`` the caller can drop straight into
   ``write_quick_config``.

The ``--byok`` flag on ``windy go`` skips all of this. That's the
escape hatch for power users who want to bring their own key — they
should never be forced through Pro.

Network errors, missing signing secret, 4xx/5xx responses, and malformed
bodies are all non-fatal: we return ``None`` and log at debug level.
The "paste a key" flow is always a viable fallback.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Env var names the quickstart writes to .env. Must match
# ``quickstart.KEY_PATTERNS[*]["env_var"]``.
PROVIDER_TO_ENV = {
    "openai":    "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "grok":      "GROK_API_KEY",
    "xai":       "GROK_API_KEY",
    "gemini":    "GEMINI_API_KEY",
    "google":    "GEMINI_API_KEY",
    "deepseek":  "DEEPSEEK_API_KEY",
    "mistral":   "MISTRAL_API_KEY",
}

# Default fallback models per provider when Pro's broker doesn't pin one.
PROVIDER_DEFAULT_MODEL = {
    "openai":    "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-latest",
    "grok":      "grok-3-mini",
    "xai":       "grok-3-mini",
    "gemini":    "gemini-2.5-flash",
    "google":    "gemini-2.5-flash",
    "deepseek":  "deepseek-chat",
    "mistral":   "mistral-large-latest",
}


@dataclass
class BrokeredCredential:
    """A short-lived LLM credential issued by Windy Pro."""

    provider: str
    env_var: str
    api_key: str
    model: str
    expires_at: Optional[datetime] = None
    usage_cap_tokens: Optional[int] = None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at


def default_pro_config_path() -> Path:
    """Where we look for the Pro account token on disk."""
    return Path.home() / ".windypro" / "config.json"


def read_pro_config(path: Path | None = None) -> dict | None:
    """Return the parsed ``~/.windypro/config.json`` dict, or None.

    Missing / unreadable / malformed files all return None so the
    caller can fall back to the BYOK path without surfacing an error.
    """
    cfg_path = path or default_pro_config_path()
    if not cfg_path.exists():
        return None
    try:
        data = json.loads(cfg_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Could not read Pro config at %s: %s", cfg_path, exc)
        return None
    if not isinstance(data, dict):
        logger.debug("Pro config at %s is not a JSON object", cfg_path)
        return None
    return data


def has_valid_pro_token(path: Path | None = None) -> bool:
    """True iff ~/.windypro/config.json carries a Pro identity reference.

    We accept any one of ``account_token``, ``token``, or
    ``windy_identity_id`` — the first two are legacy-bearer-style
    fields still in use, the third is the canonical identity under
    the HMAC contract. The server decides whether the identity is
    still valid; we just want a gate that won't false-negative on
    configs that pre-date the HMAC migration.
    """
    cfg = read_pro_config(path)
    if cfg is None:
        return False
    for key in ("account_token", "token", "windy_identity_id"):
        value = cfg.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _parse_expires_at(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    # Pro returns RFC 3339 with "Z" suffix; Python 3.11+ can parse it
    # directly via fromisoformat after the Z→+00:00 swap.
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# Default scope / TTL for hatch-time credential issuance. Pro's
# verifyBrokerSignature doesn't care what these are — it signs the
# request body bytes — but pinning them here keeps request shape
# deterministic, which makes signatures reproducible in tests.
DEFAULT_SCOPE = "hatch"
DEFAULT_DURATION_SECONDS = 86_400  # 24h; matches the broker TTL contract


def sign_broker_request(body: bytes, secret: str) -> str:
    """Return the ``X-Windy-Signature`` header value for ``body``.

    Mirrors the ``sha256=<hex>`` layout that ``trust.verify.verify_hmac``
    accepts on the inbound side, so the pattern is symmetric across
    this repo's HMAC'd traffic. Exposed for tests.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _resolve_signing_secret() -> str:
    """Where the shared HMAC secret lives. Order:

    1. ``WINDY_BROKER_SIGNING_SECRET`` — the documented env var
    2. ``WINDY_PRO_SIGNING_SECRET``   — legacy name, kept for transition
    """
    return (
        os.environ.get("WINDY_BROKER_SIGNING_SECRET", "")
        or os.environ.get("WINDY_PRO_SIGNING_SECRET", "")
    )


def fetch_broker_credential(
    *,
    config_path: Path | None = None,
    pro_base_url: str | None = None,
    http_client=None,  # injected in tests
    timeout_seconds: float = 5.0,
    windy_identity_id: str | None = None,
    passport_number: str | None = None,
    scope: str = DEFAULT_SCOPE,
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
    signing_secret: str | None = None,
) -> Optional[BrokeredCredential]:
    """Exchange a Pro account identity for a short-lived LLM credential.

    HMAC-SHA256 signs the raw request body with ``signing_secret``
    (default: ``WINDY_BROKER_SIGNING_SECRET`` env var). The identity
    fields — ``windy_identity_id`` and ``passport_number`` — default to
    values from the Pro config file so first-time callers don't need to
    pass them explicitly.

    Returns ``None`` on any failure (no config, no signing secret, no
    network, 4xx/5xx, malformed response). Callers should treat ``None``
    as "managed credentials aren't available — prompt for a key instead."
    """
    cfg = read_pro_config(config_path)
    if cfg is None:
        return None

    # The identity reference in the Pro config — used both as a sanity
    # check (is there a paired Pro account on this machine?) and as the
    # default windy_identity_id for the request.
    if not (cfg.get("account_token") or cfg.get("token") or cfg.get("windy_identity_id")):
        logger.debug("Pro config has no account_token/windy_identity_id — skipping broker")
        return None

    secret = signing_secret if signing_secret is not None else _resolve_signing_secret()
    if not secret:
        logger.debug(
            "No WINDY_BROKER_SIGNING_SECRET configured — cannot sign broker request"
        )
        return None

    identity = (
        windy_identity_id
        or cfg.get("windy_identity_id")
        or cfg.get("account_token")
        or cfg.get("token")
        or ""
    )
    passport = passport_number or cfg.get("passport_number") or os.environ.get(
        "ETERNITAS_PASSPORT", "",
    )

    base = pro_base_url or cfg.get("base_url") or os.environ.get(
        "WINDY_API_URL", "http://localhost:8098",
    )
    url = f"{base.rstrip('/')}/api/v1/agent/credentials/issue"

    payload = {
        "windy_identity_id": identity,
        "passport_number":   passport,
        "scope":             scope,
        "duration_seconds":  duration_seconds,
    }
    # Canonicalise the bytes we sign — the HMAC is over the *exact
    # bytes* Pro's verifier will see, so we must POST these bytes, not
    # re-serialize via httpx's json= parameter (which could emit
    # whitespace differently between Python versions).
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    # Record timestamp *before* the HMAC so signing + timestamp are
    # derived from the same instant. Pro enforces a 300s replay window
    # against this header; the signature itself is over the body only
    # (unchanged from the original contract).
    timestamp = int(time.time())
    signature = sign_broker_request(body, secret)

    if http_client is None:
        try:
            import httpx
            http_client = httpx.Client(timeout=timeout_seconds)
            own_client = True
        except ImportError:
            logger.debug("httpx not available — cannot call Pro broker")
            return None
    else:
        own_client = False

    try:
        response = http_client.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Windy-Signature": signature,
                # Replay-protection header — Pro rejects requests with
                # |now - ts| > 300s. Sent as unix seconds in a string,
                # matching the ecosystem's outbound-webhook convention.
                "X-Windy-Timestamp": str(timestamp),
            },
        )
    except Exception as exc:
        logger.debug("Pro broker request failed: %s", exc)
        if own_client:
            try:
                http_client.close()
            except Exception:
                pass
        return None

    if own_client:
        try:
            http_client.close()
        except Exception:
            pass

    status = getattr(response, "status_code", 0)
    if status != 200:
        logger.debug("Pro broker returned %s (not 200) — falling back", status)
        return None

    try:
        data = response.json()
    except Exception as exc:
        logger.debug("Pro broker response was not JSON: %s", exc)
        return None

    provider = (data.get("provider") or "").lower()
    api_key = data.get("broker_token") or ""
    if not provider or not api_key:
        logger.debug("Pro broker response missing provider/broker_token")
        return None

    env_var = PROVIDER_TO_ENV.get(provider)
    if env_var is None:
        logger.debug("Pro broker returned unknown provider: %s", provider)
        return None

    model = data.get("model") or PROVIDER_DEFAULT_MODEL.get(provider) or ""
    if not model:
        logger.debug("Pro broker didn't pin a model and we have no default")
        return None

    usage_cap = data.get("usage_cap_tokens")
    if not isinstance(usage_cap, int):
        usage_cap = None

    return BrokeredCredential(
        provider=provider,
        env_var=env_var,
        api_key=api_key,
        model=model,
        expires_at=_parse_expires_at(data.get("expires_at")),
        usage_cap_tokens=usage_cap,
    )
