"""Bot API credentials — unified session for agent-to-ecosystem calls.

Mints and rotates a wk_-prefixed bot key via the windy-pro
account-server. The owner's JWT is used once at mint time; every
ecosystem call thereafter (Mail send, Cloud archive, Chat message)
authenticates with the bot key, not the owner's session.

Cache lives at data/bot_key.json. Rotation happens automatically when
the cached key is within 30 days of expiry.

Account-server contract (POST {WINDY_PRO_URL}/api/v1/identity/bot-keys/mint):
    Headers: Authorization: Bearer <owner_jwt>
    Body:    {"passport_number": "ET-00001"}
    Response: {"bot_key": "wk_...", "expires_at": "2027-04-16T00:00:00Z",
               "windy_identity_id": "..."}
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)

PROJECT_ROOT = get_project_root()
_CACHE_FILE = PROJECT_ROOT / "data" / "bot_key.json"
_ROTATION_WINDOW = timedelta(days=30)
_TIMEOUT = 10.0


@dataclass
class BotCredential:
    bot_key: str
    expires_at: datetime
    windy_identity_id: str = ""
    passport_number: str = ""
    key_id: str = ""
    scopes: list[str] = field(default_factory=list)

    def needs_rotation(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.expires_at - now <= _ROTATION_WINDOW

    def has_scope(self, scope: str) -> bool:
        """True if this credential grants `scope` (or a wildcard covering it)."""
        if not self.scopes:
            return False
        if "*" in self.scopes or scope in self.scopes:
            return True
        prefix = scope.split(":", 1)[0] + ":*"
        return prefix in self.scopes

    def to_dict(self) -> dict:
        return {
            "bot_key": self.bot_key,
            "expires_at": self.expires_at.isoformat(),
            "windy_identity_id": self.windy_identity_id,
            "passport_number": self.passport_number,
            "key_id": self.key_id,
            "scopes": list(self.scopes),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BotCredential":
        return cls(
            bot_key=data["bot_key"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            windy_identity_id=data.get("windy_identity_id", ""),
            passport_number=data.get("passport_number", ""),
            key_id=data.get("key_id", ""),
            scopes=list(data.get("scopes", [])),
        )


def _pro_url() -> str:
    url = os.environ.get("WINDY_PRO_URL", "") or os.environ.get("WINDY_API_URL", "")
    return url.rstrip("/")


def _load_cached() -> BotCredential | None:
    if not _CACHE_FILE.exists():
        return None
    try:
        return BotCredential.from_dict(json.loads(_CACHE_FILE.read_text()))
    except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
        logger.debug("Bot key cache unreadable, will re-mint: %s", exc)
        return None


def _save_cached(cred: BotCredential) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cred.to_dict(), indent=2))
    try:
        _CACHE_FILE.chmod(0o600)
    except OSError:
        pass


DEFAULT_SCOPES = [
    "mail:send",
    "chat:read",
    "chat:write",
    "cloud:upload",
    "cloud:download",
]


async def mint_bot_key(
    owner_jwt: str,
    passport_number: str,
    scopes: list[str] | None = None,
    pro_url: str | None = None,
) -> BotCredential:
    """Mint a fresh wk_ bot key from the account-server.

    `scopes` is a list of requested permissions (e.g. "mail:send",
    "cloud:upload"). The account-server MAY downscope based on the
    passport's current integrity band — callers must check the
    returned credential's `scopes` rather than assuming they got what
    they asked for.
    """
    url = (pro_url or _pro_url()).rstrip("/")
    if not url:
        raise RuntimeError("WINDY_PRO_URL not configured")
    if not owner_jwt:
        raise RuntimeError("owner JWT required to mint bot key")
    if not passport_number:
        raise RuntimeError("passport_number required to mint bot key")

    requested = list(scopes) if scopes is not None else list(DEFAULT_SCOPES)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{url}/api/v1/identity/bot-keys/mint",
            json={"passport_number": passport_number, "scopes": requested},
            headers={"Authorization": f"Bearer {owner_jwt}"},
        )
        resp.raise_for_status()
        data = resp.json()

    granted = list(data.get("scopes", requested))
    if granted != requested:
        logger.info("Bot key downscoped by server: requested %s, granted %s", requested, granted)

    cred = BotCredential(
        bot_key=data["bot_key"],
        expires_at=datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")),
        windy_identity_id=data.get("windy_identity_id", ""),
        passport_number=passport_number,
        key_id=data.get("key_id", ""),
        scopes=granted,
    )
    _save_cached(cred)
    logger.info(
        "Minted bot key %s (id=%s, scopes=%s, expires %s)",
        cred.bot_key[:8] + "…", cred.key_id or "-", ",".join(cred.scopes) or "-",
        cred.expires_at.isoformat(),
    )
    return cred


async def get_bot_key(
    owner_jwt: str | None = None,
    passport_number: str | None = None,
) -> BotCredential | None:
    """Return a valid cached bot key, rotating if within the 30-day window.

    Returns None if no cache exists and minting prerequisites are
    missing — callers should fall back to the owner JWT with a warning.
    """
    cached = _load_cached()
    if cached and not cached.needs_rotation():
        return cached

    jwt = owner_jwt or os.environ.get("WINDY_JWT", "")
    passport = passport_number or (cached.passport_number if cached else "") or os.environ.get("ETERNITAS_PASSPORT", "")

    if not jwt or not passport:
        if cached:
            logger.warning("Bot key expiring soon but cannot rotate (missing JWT/passport); using stale key")
            return cached
        return None

    try:
        return await mint_bot_key(jwt, passport)
    except Exception as exc:
        logger.warning("Bot key mint failed: %s", exc)
        return cached


def clear_cached_bot_key() -> None:
    """Remove the cached bot key (useful for tests and sign-out)."""
    _CACHE_FILE.unlink(missing_ok=True)


async def revoke_bot_key(
    key_id: str,
    reason: str,
    owner_jwt: str | None = None,
    pro_url: str | None = None,
    cascade_webhook_urls: list[str] | None = None,
) -> dict:
    """Revoke a wk_ bot key at the account-server.

    POST {WINDY_PRO_URL}/api/v1/identity/bot-keys/revoke
        Body: {key_id, reason}

    Also POSTs a cascade webhook to any platforms where the key was
    last seen so they drop cached auth. Clears the local cache when
    the revoked key matches it.
    """
    url = (pro_url or _pro_url()).rstrip("/")
    if not url:
        raise RuntimeError("WINDY_PRO_URL not configured")
    jwt = owner_jwt or os.environ.get("WINDY_JWT", "")
    if not jwt:
        raise RuntimeError("owner JWT required to revoke bot key")
    if not key_id:
        raise RuntimeError("key_id required")

    summary = {"revoked": False, "cascade": {}}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{url}/api/v1/identity/bot-keys/revoke",
            json={"key_id": key_id, "reason": reason},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        summary["revoked"] = resp.status_code in (200, 204)
        if not summary["revoked"]:
            logger.warning("Revoke returned %s: %s", resp.status_code, resp.text[:200])

        for webhook in cascade_webhook_urls or []:
            try:
                wh = await client.post(
                    webhook,
                    json={"event": "bot_key.revoked", "key_id": key_id, "reason": reason},
                )
                summary["cascade"][webhook] = wh.status_code
            except httpx.RequestError as exc:
                summary["cascade"][webhook] = f"error: {exc.__class__.__name__}"

    cached = _load_cached()
    if cached and cached.key_id == key_id:
        clear_cached_bot_key()
        logger.info("Cleared local cache for revoked key %s", key_id)

    return summary


async def rotate_on_trust_change(new_band: str) -> BotCredential | None:
    """Re-mint the wk_ key after a trust band change.

    Called from the Eternitas trust.changed webhook handler. The new
    band may unlock or revoke scopes, so we re-mint rather than patch
    the existing key.
    """
    cached = _load_cached()
    passport = (cached.passport_number if cached else "") or os.environ.get("ETERNITAS_PASSPORT", "")
    jwt = os.environ.get("WINDY_JWT", "")
    if not passport or not jwt:
        logger.info("Trust-change rotation skipped: no passport/JWT")
        return None

    logger.info("Rotating bot key after trust band change: %s", new_band)
    try:
        return await mint_bot_key(owner_jwt=jwt, passport_number=passport)
    except Exception as exc:
        logger.warning("Trust-change rotation failed: %s", exc)
        return None


async def ecosystem_auth_header(fallback_token: str = "") -> dict[str, str]:
    """Return the Authorization header dict for an agent-to-ecosystem call.

    Prefers the cached wk_ bot key; rotates if expiring within 30 days;
    falls back to the caller's supplied token (service token or owner
    JWT) when no bot key is available. Empty dict if nothing is set so
    callers can no-op the header.
    """
    cred = await get_bot_key()
    if cred:
        return {"Authorization": f"Bearer {cred.bot_key}"}
    if fallback_token:
        return {"Authorization": f"Bearer {fallback_token}"}
    return {}
