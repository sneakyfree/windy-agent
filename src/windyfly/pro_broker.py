"""Windy Pro managed-credential broker client.

Used by ``windy go`` to skip the "paste your API key" prompt when the
user already has a Windy Pro account. The flow:

1. Look for ``~/.windypro/config.json``. If missing → user has no Pro
   account on this machine; fall through to the paste-a-key flow.
2. Read the Pro account token from the config. If missing or expired
   → fall through.
3. POST to ``<pro_base>/api/v1/broker/llm-credentials`` with the token.
   The response shape is:
       {
         "provider":   "anthropic",        // one of openai/anthropic/...
         "api_key":    "wk_broker_...",    // short-lived, 24h TTL
         "model":      "claude-3-5-sonnet-latest",
         "expires_at": "2026-04-19T14:32:07Z"
       }
4. Return a ``BrokeredCredential`` the caller can drop straight into
   ``write_quick_config``.

The ``--byok`` flag on ``windy go`` skips all of this. That's the
escape hatch for power users who want to bring their own key — they
should never be forced through Pro.

Network errors, expired tokens, and malformed responses are all
non-fatal: we return ``None`` and log at debug level. The "paste a key"
flow is always a viable fallback.
"""

from __future__ import annotations

import json
import logging
import os
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
    """True iff ~/.windypro/config.json has a non-empty account token.

    The server will reject expired tokens, so we don't check expiry
    client-side — we just require the token string to be present.
    """
    cfg = read_pro_config(path)
    if cfg is None:
        return False
    token = cfg.get("account_token") or cfg.get("token") or ""
    return bool(token and isinstance(token, str) and token.strip())


def _parse_expires_at(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    # Pro returns RFC 3339 with "Z" suffix; Python 3.11+ can parse it
    # directly via fromisoformat after the Z→+00:00 swap.
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_broker_credential(
    *,
    config_path: Path | None = None,
    pro_base_url: str | None = None,
    http_client=None,  # injected in tests
    timeout_seconds: float = 5.0,
) -> Optional[BrokeredCredential]:
    """Exchange a Pro account token for a short-lived LLM credential.

    Returns ``None`` on any failure (no config, no network, expired
    token, malformed response). Callers should treat ``None`` as
    "managed credentials aren't available — prompt for a key instead."
    """
    cfg = read_pro_config(config_path)
    if cfg is None:
        return None

    token = cfg.get("account_token") or cfg.get("token") or ""
    if not token:
        logger.debug("Pro config has no account_token — skipping broker")
        return None

    base = pro_base_url or cfg.get("base_url") or os.environ.get(
        "WINDY_API_URL", "http://localhost:8098",
    )
    url = f"{base.rstrip('/')}/api/v1/broker/llm-credentials"

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
            headers={"Authorization": f"Bearer {token}"},
            json={"purpose": "hatch"},
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
    api_key = data.get("api_key") or ""
    if not provider or not api_key:
        logger.debug("Pro broker response missing provider/api_key")
        return None

    env_var = PROVIDER_TO_ENV.get(provider)
    if env_var is None:
        logger.debug("Pro broker returned unknown provider: %s", provider)
        return None

    model = data.get("model") or PROVIDER_DEFAULT_MODEL.get(provider) or ""
    if not model:
        logger.debug("Pro broker didn't pin a model and we have no default")
        return None

    return BrokeredCredential(
        provider=provider,
        env_var=env_var,
        api_key=api_key,
        model=model,
        expires_at=_parse_expires_at(data.get("expires_at")),
    )
