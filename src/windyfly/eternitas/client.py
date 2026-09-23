"""Eternitas API client — talks to the real Eternitas registry service.

Eternitas is a third-party identity platform, not part of the Windy
ecosystem: it issues, it does not orchestrate. This client is how we ask.

**Two minting doors, and picking the wrong one breaks a fresh install:**

* ``auto_hatch()`` → ``POST /bots/auto-hatch`` — the CONSUMER door. Anonymous,
  creates the self-registered operator for us. This is what every hatch uses,
  in this repo and in windy-pro.
* ``register()`` → ``POST /bots/register`` — the ENTERPRISE door. Requires
  ``ETERNITAS_OPERATOR_KEY`` for an already-VERIFIED operator. That key is
  blank on a fresh machine, so a consumer hatch through this door 401s and
  produces no passport. Kept for programmatic callers who hold such a key.
"""

from __future__ import annotations

import logging
import os

import httpx

from windyfly.eternitas.models import (
    BotIdentity,
    EternitasPassport,
    RegistrationRequest,
    RevocationResult,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
# Registration is measurably slow on prod (~30s): operator X-API-Key auth
# bcrypt-scans every operator row (eternitas issue — O(n) auth). 10s made
# the desktop hatch time out AFTER the server had already minted the
# passport+certificate. Generous ceiling until eternitas indexes the key.
_REGISTER_TIMEOUT = 60.0



class HatchAuthRequired(RuntimeError):
    """Eternitas refused the hatch for lack of (or a rejected) sign-in."""


def auto_hatch_credential() -> tuple[str, str]:
    """Pick the Bearer for ``/bots/auto-hatch`` → (token, source label).

    Precedence:
      1. ``ETERNITAS_OPERATOR_JWT``: an explicit operator credential wins.
      2. ``WINDY_HUB_JWT``: a hub login token handed in by a caller
         (shape-checked: RS256, type human, carries a Windy identity).
      3. The stored ``windy login`` session (refreshed if near expiry).
      4. Nothing. That's the anonymous door, which Eternitas is closing.
    The token itself is never logged, only the source label.
    """
    operator_jwt = os.environ.get("ETERNITAS_OPERATOR_JWT", "").strip()
    if operator_jwt:
        return operator_jwt, "operator JWT"
    from windyfly import hub_login

    hub_jwt = os.environ.get("WINDY_HUB_JWT", "").strip()
    if hub_jwt and hub_login.looks_like_hub_human_token(hub_jwt):
        return hub_jwt, "WINDY_HUB_JWT"
    try:
        session_token = hub_login.get_access_token()
    except Exception as exc:  # never let a sign-in problem crash the hatch
        logger.debug("hub session lookup failed: %s", exc)
        session_token = None
    if session_token:
        return session_token, "windy login session"
    return "", "none"

class EternitasClient:
    """HTTP client for the Eternitas bot registry API.

    When the real Eternitas service is deployed, this client talks to it.
    For local development, use MockEternitasClient instead.
    """

    def __init__(self, api_url: str | None = None, operator_key: str | None = None) -> None:
        from windyfly.eternitas.url import resolve_eternitas_url

        self.api_url = (api_url or resolve_eternitas_url("https://api.eternitas.ai")).rstrip("/")
        self.operator_key = operator_key or os.environ.get("ETERNITAS_OPERATOR_KEY", "")
        self.admin_token = os.environ.get("ETERNITAS_ADMIN_TOKEN", "")

    def _reg_headers(self) -> dict[str, str]:
        """Headers for registration (operator API key)."""
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.operator_key:
            h["X-API-Key"] = self.operator_key
        return h

    def _admin_headers(self) -> dict[str, str]:
        """Headers for admin endpoints (Bearer token)."""
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.admin_token:
            h["Authorization"] = f"Bearer {self.admin_token}"
        return h

    async def register(self, request: RegistrationRequest) -> EternitasPassport:
        """Register a new bot and receive a passport.

        POST /api/v1/bots/register
        Auth: X-API-Key (operator key)
        """
        try:
            async with httpx.AsyncClient(timeout=_REGISTER_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.api_url}/api/v1/bots/register",
                    json=request.to_api_payload(),
                    headers=self._reg_headers(),
                )
                resp.raise_for_status()
                return EternitasPassport.from_api_response(resp.json())
        except httpx.ConnectError as e:
            logger.error("Eternitas registration connection error: %s", e)
            raise
        except httpx.HTTPStatusError as e:
            logger.error("Eternitas registration failed: %s", e)
            raise

    async def auto_hatch(self, request: RegistrationRequest) -> EternitasPassport:
        """Hatch a passport through the CONSUMER door.

        POST /api/v1/bots/auto-hatch
        Auth: none required — this is the anonymous human door, and the
        route's own docstring calls it "the normie path for `windy go`
        hatches". It creates the self-registered operator for us.

        This is the door the browser and mobile lanes already come through
        (via windy-pro), so the terminal door using it is what "one issuer,
        one door" means. The alternative, /bots/register, demands an
        operator API key belonging to an already-VERIFIED operator —
        `ETERNITAS_OPERATOR_KEY` is blank on a fresh machine, so the
        terminal door simply 401d and produced no passport.

        A credential is sent when one is available (see
        ``auto_hatch_credential``). Eternitas is closing the anonymous door
        (``AUTO_HATCH_REQUIRE_PRO_JWT``), after which an unauthenticated
        call gets 401. The terminal's credential is the owner's hub sign-in
        (``windy login``).
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        bearer, source = auto_hatch_credential()
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        logger.info("Eternitas auto-hatch credential: %s", source)

        payload = request.to_auto_hatch_payload()
        # Only relevant if the deployment has turned Turnstile on. A terminal
        # has no browser to solve a challenge in, so this is normally empty
        # and the gate is expected to be satisfied by the JWT above instead.
        turnstile_token = os.environ.get("ETERNITAS_TURNSTILE_TOKEN", "")
        if turnstile_token:
            payload["turnstile_token"] = turnstile_token

        try:
            async with httpx.AsyncClient(timeout=_REGISTER_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.api_url}/api/v1/bots/auto-hatch",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                return EternitasPassport.from_api_response(resp.json())
        except httpx.ConnectError as e:
            logger.error("Eternitas auto-hatch connection error: %s", e)
            raise
        except httpx.HTTPStatusError as e:
            # Translate the gate responses into something a person can act
            # on. These surface in the hatch's error list and, for the
            # terminal door, on a human's screen.
            status = e.response.status_code
            if status == 401:
                if not bearer:
                    raise HatchAuthRequired(
                        "Eternitas now requires you to sign in with your Windy "
                        "account (account.windyword.ai) to hatch an agent. Run "
                        "`windy login`, then run "
                        "this again (an interactive `windy go` asks you to sign "
                        "in automatically)."
                    ) from e
                raise HatchAuthRequired(
                    f"Eternitas didn't accept your Windy sign-in ({source}). It "
                    "may have expired or not be a personal account. Run "
                    "`windy login` again, then retry."
                ) from e
            if status == 403:
                raise RuntimeError(
                    "Eternitas could not confirm a human is running this hatch. "
                    "Hatch from the web app at account.windyword.ai, or run "
                    "`windy login` to sign in from the terminal."
                ) from e
            if status == 429:
                raise RuntimeError(
                    "Eternitas rate limit: at most 5 hatches per hour. Wait an "
                    "hour and try again."
                ) from e
            logger.error("Eternitas auto-hatch failed: %s", e)
            raise

    async def verify(self, passport_id: str) -> EternitasPassport | None:
        """Verify a passport is valid and active.

        GET /api/v1/registry/verify/{passport}
        No auth required (public endpoint).
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.api_url}/api/v1/registry/verify/{passport_id}",
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return EternitasPassport.from_api_response(resp.json())
        except httpx.ConnectError as e:
            logger.error("Eternitas verify connection error: %s", e)
            return None

    async def get_certificate(self, passport_id: str) -> dict:
        """Fetch the certificate of record already minted for a passport.

        GET /api/v1/certificates/{passport}
        No auth required (public endpoint).

        Used by the pre-allocated-passport lane: when another door started
        the ceremony and minted the passport, its certificate already
        exists, so we FETCH it rather than mint a second one. Returns {}
        when the certificate is absent (404) or Eternitas is unreachable —
        never raises, because a missing certificate must not take the
        ceremony down.
        """
        if not passport_id:
            return {}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.api_url}/api/v1/certificates/{passport_id}",
                )
                if resp.status_code == 404:
                    return {}
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, dict) else {}
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Eternitas certificate fetch failed for %s: %s", passport_id, e)
            return {}

    async def lookup(self, agent_name: str) -> BotIdentity | None:
        """Look up a bot's public identity by name."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.api_url}/api/v1/lookup",
                    params={"agent_name": agent_name},
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return BotIdentity.model_validate(resp.json())
        except httpx.ConnectError as e:
            logger.error("Eternitas lookup connection error: %s", e)
            return None

    async def revoke(self, passport_id: str, reason: str = "") -> RevocationResult:
        """Revoke a passport and trigger cascade teardown of services.

        POST /api/v1/admin/revoke/{passport}
        Auth: Authorization: Bearer <admin_token>
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.api_url}/api/v1/admin/revoke/{passport_id}",
                    json={"reason": reason},
                    headers=self._admin_headers(),
                )
                resp.raise_for_status()
                return RevocationResult.model_validate(resp.json())
        except httpx.ConnectError as e:
            logger.error("Eternitas revoke connection error: %s", e)
            return RevocationResult(passport_id=passport_id, error=str(e))

    async def update_services(
        self, passport_id: str, services: dict[str, str]
    ) -> EternitasPassport:
        """Update the provisioned services record for a passport."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.patch(
                    f"{self.api_url}/api/v1/passport/{passport_id}/services",
                    json=services,
                    headers=self._admin_headers(),
                )
                resp.raise_for_status()
                return EternitasPassport.from_api_response(resp.json())
        except httpx.ConnectError as e:
            logger.error("Eternitas update_services connection error: %s", e)
            raise
        except httpx.HTTPStatusError as e:
            logger.error("Eternitas update_services failed: %s", e)
            raise
