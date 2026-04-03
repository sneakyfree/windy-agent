"""OAuth token management for Anthropic Claude.ai authentication.

Handles access token storage, expiry checks, and automatic refresh
so that Windy Fly can use a Claude Max subscription instead of API billing.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Refresh the token 5 minutes before it actually expires
_EXPIRY_BUFFER_MS = 5 * 60 * 1000

# Where to persist refreshed tokens so they survive restarts
_TOKEN_CACHE_PATH = Path(os.environ.get(
    "WINDYFLY_OAUTH_CACHE",
    "data/.anthropic_oauth.json",
))

ANTHROPIC_TOKEN_ENDPOINT = "https://console.anthropic.com/v1/oauth/token"


class OAuthManager:
    """Manages Anthropic OAuth tokens with automatic refresh."""

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        expires_at: int | None = None,
    ) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        # expires_at is in milliseconds since epoch
        self._expires_at = expires_at or 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def access_token(self) -> str:
        """Return a valid access token, refreshing first if needed."""
        if self._is_expired():
            self._refresh()
        return self._access_token

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_expired(self) -> bool:
        if self._expires_at == 0:
            return False  # no expiry info — assume valid
        now_ms = int(time.time() * 1000)
        return now_ms >= (self._expires_at - _EXPIRY_BUFFER_MS)

    def _refresh(self) -> None:
        """Exchange the refresh token for a new access token."""
        logger.info("OAuth access token expired or near expiry — refreshing...")
        try:
            resp = httpx.post(
                ANTHROPIC_TOKEN_ENDPOINT,
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                },
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            self._access_token = data["access_token"]
            if "refresh_token" in data:
                self._refresh_token = data["refresh_token"]
            if "expires_at" in data:
                self._expires_at = data["expires_at"]
            elif "expires_in" in data:
                self._expires_at = int(time.time() * 1000) + data["expires_in"] * 1000

            self._save_cache()
            logger.info("OAuth token refreshed successfully.")
        except Exception as e:
            logger.exception("Failed to refresh OAuth token: %s", e)
            raise RuntimeError(
                "Could not refresh Anthropic OAuth token. "
                "Run 'claude auth login --claudeai' to re-authenticate."
            )

    def _save_cache(self) -> None:
        """Persist current tokens to disk so they survive restarts."""
        try:
            _TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _TOKEN_CACHE_PATH.write_text(json.dumps({
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "expires_at": self._expires_at,
            }))
            _TOKEN_CACHE_PATH.chmod(0o600)
        except OSError:
            logger.warning("Could not persist refreshed OAuth tokens to %s", _TOKEN_CACHE_PATH)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

_manager: OAuthManager | None = None


def get_oauth_manager(config: dict[str, Any] | None = None) -> OAuthManager | None:
    """Return a shared OAuthManager if OAuth credentials are configured.

    Checks (in order):
        1. Environment variables
        2. Token cache file (from a previous refresh)

    Returns None if OAuth is not configured — caller should fall back to API key.
    """
    global _manager
    if _manager is not None:
        return _manager

    access_token = os.environ.get("ANTHROPIC_OAUTH_ACCESS_TOKEN", "")
    refresh_token = os.environ.get("ANTHROPIC_OAUTH_REFRESH_TOKEN", "")
    expires_at = int(os.environ.get("ANTHROPIC_OAUTH_EXPIRES_AT", "0") or "0")

    # Try loading from cache if env vars are missing
    if not access_token and _TOKEN_CACHE_PATH.exists():
        try:
            cached = json.loads(_TOKEN_CACHE_PATH.read_text())
            access_token = cached.get("access_token", "")
            refresh_token = refresh_token or cached.get("refresh_token", "")
            expires_at = expires_at or cached.get("expires_at", 0)
        except (json.JSONDecodeError, OSError):
            pass

    if not access_token:
        return None

    _manager = OAuthManager(access_token, refresh_token, expires_at)
    return _manager
