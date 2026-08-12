"""Bot API credentials — unified session for agent-to-ecosystem calls.

Mints and rotates a wk_-prefixed bot key via the windy-pro
account-server. The owner's JWT is used once at mint time; every
ecosystem call thereafter (Mail send, Cloud archive, Chat message)
authenticates with the bot key, not the owner's session.

Cache lives at data/bot_key.json. Rotation happens automatically when
the cached key is within 30 days of expiry.

Account-server contract (POST {WINDY_PRO_URL}/api/v1/identity/api-keys):
    Headers: Authorization: Bearer <owner_jwt>   ← the OPERATOR's JWT
    Body:    {"identityId": "<bot identity id>",
              "scopes": ["mail:send", ...],
              "label": "windy-fly ET-00001",
              "expiresInDays": 365}
    201:     {"apiKey": "wk_...", "keyPrefix": "wk_xxxxxxxx", "id": "<key id>",
              "scopes": [...], "expiresAt": "2027-04-16T00:00:00Z",
              "warning": "Store this API key securely..."}

    Source of truth: windy-pro `account-server/src/routes/identity.ts`
    (`router.post('/api-keys', ...)`). This module used to POST to
    `/api/v1/identity/bot-keys/mint`, which has never existed — every
    mint 404'd.

`identityId` must be the BOT's windy-pro identity (`users.identity_type
== 'bot'`): the server 400s on a human identity ("API keys can only be
created for bot identities") and 403s unless the caller is that bot's
operator or an admin. windy-pro has no passport → bot-identity lookup,
and a JWT's `sub` is the OWNER, so the id has to be handed in — see
`_resolve_bot_identity_id`. A terminal-lane (`windy go`) hatch never
creates a windy-pro bot row at all, so it has no id to hand in and
minting is SKIPPED — visibly, via `BotIdentityUnavailable`, never
silently.

`expiresInDays` is not optional in practice: the server only returns
`expiresAt` when it was sent, and the local rotation window needs one.

Revocation (DELETE {WINDY_PRO_URL}/api/v1/identity/api-keys/<key id>):
    Headers: Authorization: Bearer <owner_jwt>
    200:     {"revoked": true|false}   ← false means "no such key id"

    This module used to POST `/api/v1/identity/bot-keys/revoke`, equally
    fictional, and counted any 2xx as success — so every `windy keys
    rotate --hard` reported a revocation that never happened. See
    `revoke_bot_key` for the confirmed-only contract and for the
    measured limit: a revocation does NOT propagate beyond windy-pro.

Those two are the only account-server routes this module talks to; both
are verified against `account-server/src/routes/identity.ts`. Anything
added here must be checked against that file — this module has now
shipped two invented routes.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)

PROJECT_ROOT = get_project_root()
_CACHE_FILE = PROJECT_ROOT / "data" / "bot_key.json"
_ROTATION_WINDOW = timedelta(days=30)
_TIMEOUT = 10.0
# The account-server only stamps an expiry when the request asks for
# one, and `needs_rotation()` needs a real datetime to work with.
_DEFAULT_EXPIRY_DAYS = 365


class BotIdentityUnavailable(RuntimeError):
    """No windy-pro bot identity id is available to mint a key against.

    NOT a failure — it is the honest steady state of a terminal-lane
    (`windy go`) agent, which exists in Eternitas but has no bot row at
    windy-pro. Raised instead of guessing an identity (the owner's id
    400s) or pretending the mint succeeded. Callers should report it as
    a SKIP, distinct from a mint that was attempted and failed.
    """


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
        return BotCredential.from_dict(json.loads(_CACHE_FILE.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
        logger.debug("Bot key cache unreadable, will re-mint: %s", exc)
        return None


def _save_cached(cred: BotCredential) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cred.to_dict(), indent=2), encoding="utf-8")
    try:
        _CACHE_FILE.chmod(0o600)
    except OSError:
        pass


def _resolve_bot_identity_id(explicit: str | None = None) -> str:
    """Find the BOT's windy-pro identity id, or "" if we don't have one.

    Order: explicit argument (the remote/browser handoff carries it in
    the `/hatch/remote` payload) → BOT_IDENTITY_ID env → the identity a
    previous successful mint cached.

    There is deliberately NO fallback to the owner: `WINDY_IDENTITY_ID`
    and a JWT's `sub` claim are both the OPERATOR's identity, and the
    account-server 400s on those ("API keys can only be created for bot
    identities"). Nor is there a passport → bot-identity lookup to fall
    back on — windy-pro exposes none (`/owns-passport/:passport` returns
    only the caller's own id).
    """
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    env_id = (
        os.environ.get("BOT_IDENTITY_ID", "")
        or os.environ.get("WINDY_BOT_IDENTITY_ID", "")
    ).strip()
    if env_id:
        return env_id
    cached = _load_cached()
    if cached and cached.windy_identity_id:
        return cached.windy_identity_id
    return ""


def _parse_expiry(raw: Any, expires_in_days: int) -> datetime:
    """Parse the server's `expiresAt`, falling back to what we asked for.

    The account-server omits `expiresAt` entirely when no
    `expiresInDays` was sent. We always send one, so a missing value
    means an older server — say so rather than crashing on a KeyError.
    """
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "Unparseable expiresAt %r from account-server; assuming %sd", raw, expires_in_days
            )
    else:
        logger.warning("Account-server returned no expiresAt; assuming %sd for rotation", expires_in_days)
    return datetime.now(timezone.utc) + timedelta(days=expires_in_days)


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
    bot_identity_id: str | None = None,
    expires_in_days: int = _DEFAULT_EXPIRY_DAYS,
) -> BotCredential:
    """Mint a fresh wk_ bot key from the account-server.

    `bot_identity_id` is the BOT's windy-pro identity — the one thing
    the server keys the whole call on. It must be supplied by whoever
    knows it (the remote/browser handoff) or via BOT_IDENTITY_ID; see
    `_resolve_bot_identity_id`. Raises `BotIdentityUnavailable` when
    there is none, so the caller can report an honest SKIP.

    `owner_jwt` is the OPERATOR's JWT — the server 403s unless the
    caller is the bot's operator or an admin.

    `scopes` is a list of requested permissions (e.g. "mail:send",
    "cloud:upload"). The account-server MAY downscope — callers must
    check the returned credential's `scopes` rather than assuming they
    got what they asked for.
    """
    url = (pro_url or _pro_url()).rstrip("/")
    if not url:
        raise RuntimeError("WINDY_PRO_URL not configured")
    if not owner_jwt:
        raise RuntimeError("owner JWT required to mint bot key")
    if not passport_number:
        raise RuntimeError("passport_number required to mint bot key")

    bot_id = _resolve_bot_identity_id(bot_identity_id)
    if not bot_id:
        # NOT a silent no-op and NOT a pretend success: the caller gets
        # a typed, self-explaining refusal it can log as a skip.
        raise BotIdentityUnavailable(
            "skipped: no bot identity id (terminal-lane agents have no windy-pro "
            f"identity yet; passport={passport_number}). Supply bot_identity_id= "
            "or set BOT_IDENTITY_ID — the remote/browser hatch carries it."
        )

    requested = list(scopes) if scopes is not None else list(DEFAULT_SCOPES)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{url}/api/v1/identity/api-keys",
            json={
                "identityId": bot_id,
                "scopes": requested,
                "label": f"windy-fly {passport_number}",
                "expiresInDays": expires_in_days,
            },
            headers={"Authorization": f"Bearer {owner_jwt}"},
        )
        resp.raise_for_status()
        data = resp.json()

    granted = list(data.get("scopes", requested))
    if granted != requested:
        logger.info("Bot key downscoped by server: requested %s, granted %s", requested, granted)

    cred = BotCredential(
        bot_key=data["apiKey"],
        expires_at=_parse_expiry(data.get("expiresAt"), expires_in_days),
        # The identity this key actually belongs to — the BOT's, not the
        # operator's. Also what makes a later rotation self-sufficient.
        windy_identity_id=bot_id,
        passport_number=passport_number,
        key_id=data.get("id", ""),
        scopes=granted,
    )
    _save_cached(cred)
    logger.info(
        "Minted bot key %s (id=%s, identity=%s, scopes=%s, expires %s)",
        data.get("keyPrefix") or cred.bot_key[:11] + "…",
        cred.key_id or "-", bot_id, ",".join(cred.scopes) or "-",
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
    except BotIdentityUnavailable as exc:
        # We hold everything EXCEPT the bot's windy-pro identity id, so
        # no mint was attempted. Say so plainly — INFO, not DEBUG, and
        # never dressed up as a success (see NO SILENT NO-OPS).
        logger.info("Bot key mint %s — using passport-token fallback", exc)
        return cached
    except RuntimeError as exc:
        # Deterministic "can't mint here" states, raised BEFORE any
        # network call: no account-server URL configured, or the only
        # credential we hold is a passport token (EPT) rather than an
        # owner JWT. This is the NORMAL steady state for a hatched agent
        # that authenticates with its Eternitas passport — bot-key
        # minting is a hatch-time step, and the caller falls back to the
        # EPT, which every platform accepts. Not an error; DEBUG so it
        # doesn't cry WARNING on every ecosystem call (was noise on every
        # Windy 0 backup — see 2026-07-06 backup investigation).
        logger.debug("Bot key not minted (%s); using passport-token fallback", exc)
        return cached
    except Exception as exc:
        # An actual mint was attempted (URL + owner JWT present) and
        # failed — network/HTTP/parse error. That IS worth a warning.
        logger.warning("Bot key mint failed unexpectedly: %s", exc)
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

    DELETE {WINDY_PRO_URL}/api/v1/identity/api-keys/<key_id>
        Headers:  Authorization: Bearer <owner_jwt>
        200 body: {"revoked": true|false}

    `key_id` is the account-server's key row id — the `id` field of the
    mint response, which `mint_bot_key` caches as `BotCredential.key_id`.
    A credential cached without one cannot be revoked at all; that is
    reported, never papered over.

    Returns a summary::

        {"revoked": bool,    # True ONLY on a server-CONFIRMED revocation
         "status": str,      # revoked | not_found | http_<code> | unconfirmed
         "key_id": str,
         "detail": str,      # always populated, always readable
         "cascade": {url: status|error},
         "cache_cleared": bool}

    `revoked` is the single load-bearing bit and it is deliberately
    pessimistic. `{"revoked": false}` (the server's way of saying "no
    such key id"), a non-2xx, an unreadable body — all of those mean the
    key must be assumed STILL LIVE, and all of them log WARNING. This
    function previously treated any 2xx as success while POSTing to
    `/api/v1/identity/bot-keys/revoke`, a route that has never existed:
    every revoke 404'd and every caller was told it worked.

    `reason` is kept for the local log and the returned detail only —
    the account-server records `revokedBy` (from the JWT) but has no
    field for a reason, and a DELETE carries no body.

    MEASURED LIMIT — REVOCATION DOES NOT PROPAGATE. A confirmed revoke
    flips `bot_api_keys.status` to 'revoked' in windy-pro's DB, and
    windy-pro's own `validateBotApiKey` rejects the key from then on.
    Nothing pushes that fact anywhere else: as of 2026-08-10 no
    `bot_key.revoked` receiver exists in windy-pro or windy-mail, so
    `cascade_webhook_urls` post into the void, and any platform holding
    a cached wk_ key keeps honouring it until it revalidates against
    windy-pro. Building that fan-out is windy-pro/Eternitas work — do
    not fake it here.
    """
    url = (pro_url or _pro_url()).rstrip("/")
    if not url:
        raise RuntimeError("WINDY_PRO_URL not configured")
    jwt = owner_jwt or os.environ.get("WINDY_JWT", "")
    if not jwt:
        raise RuntimeError("owner JWT required to revoke bot key")
    if not key_id:
        # No id means no revocable handle. Raising beats returning a
        # summary nobody reads: a key we cannot name is a key that
        # stays live for its full 365 days.
        raise RuntimeError(
            "key_id required to revoke: the account-server revokes by key id "
            "(the mint response's `id`). A credential cached without one cannot "
            "be revoked — rotate it out and let it expire, and say so."
        )

    summary: dict[str, Any] = {
        "revoked": False,
        "status": "unconfirmed",
        "key_id": key_id,
        "detail": "",
        "cascade": {},
        "cache_cleared": False,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.delete(
            f"{url}/api/v1/identity/api-keys/{quote(key_id, safe='')}",
            headers={"Authorization": f"Bearer {jwt}"},
        )

        if resp.status_code // 100 == 2:
            try:
                confirmed = bool(resp.json().get("revoked"))
                parsed = True
            except (ValueError, AttributeError):
                confirmed, parsed = False, False
            if confirmed:
                summary["revoked"] = True
                summary["status"] = "revoked"
                summary["detail"] = f"account-server confirmed revocation (reason: {reason})"
            elif parsed:
                # The ONLY way the server returns false is an unknown
                # key id — the key we meant to kill was never touched.
                summary["status"] = "not_found"
                summary["detail"] = (
                    f"account-server returned revoked=false for key_id={key_id} — no such key "
                    "row. NOTHING WAS REVOKED; assume the key is still live."
                )
            else:
                summary["detail"] = (
                    f"account-server returned {resp.status_code} with an unreadable body "
                    f"({resp.text[:120]!r}) — revocation UNCONFIRMED; assume the key is still live."
                )
        elif resp.status_code == 404:
            # The route itself, not the key: DELETE /api-keys/:keyId
            # answers 200 {"revoked": false} for an unknown id. A 404
            # means wrong base URL or an account-server without the
            # route — exactly the failure this function used to ship.
            summary["status"] = "http_404"
            summary["detail"] = (
                f"DELETE {url}/api/v1/identity/api-keys/<id> returned 404 — the route is "
                "missing or WINDY_PRO_URL is wrong. NOTHING WAS REVOKED."
            )
        else:
            summary["status"] = f"http_{resp.status_code}"
            summary["detail"] = (
                f"account-server returned {resp.status_code}: {resp.text[:160]} — "
                "NOTHING WAS REVOKED."
            )

        if summary["revoked"]:
            logger.info("Revoked bot key %s (reason: %s)", key_id, reason)
        else:
            logger.warning("Bot key %s NOT revoked — %s", key_id, summary["detail"])

        # Best-effort fan-out. No receiver exists today (see docstring);
        # the statuses are recorded so the caller can see that, not so
        # anyone can call a 404 an acknowledgement.
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
        if summary["revoked"]:
            clear_cached_bot_key()
            summary["cache_cleared"] = True
            logger.info("Cleared local cache for revoked key %s", key_id)
        else:
            # Keep it. Dropping the cache on an unconfirmed revoke throws
            # away the only handle we have on a key that is still live —
            # we could never retry the revoke.
            logger.warning(
                "Keeping cached key %s: revocation unconfirmed, so the credential is "
                "presumed live and the id is needed to retry", key_id,
            )

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
    except BotIdentityUnavailable as exc:
        # A skip, not a failure — don't cry WARNING over it.
        logger.info("Trust-change rotation %s", exc)
        return None
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
