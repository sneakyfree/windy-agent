"""Eternitas API client — talks to the real Eternitas registry service."""

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


class EternitasClient:
    """HTTP client for the Eternitas bot registry API.

    When the real Eternitas service is deployed, this client talks to it.
    For local development, use MockEternitasClient instead.
    """

    def __init__(self, api_url: str | None = None, operator_key: str | None = None) -> None:
        self.api_url = (
            api_url
            or os.environ.get("ETERNITAS_API_URL", "https://api.eternitas.ai")
        ).rstrip("/")
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
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
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
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.patch(
                f"{self.api_url}/api/v1/passport/{passport_id}/services",
                json=services,
                headers=self._admin_headers(),
            )
            resp.raise_for_status()
            return EternitasPassport.from_api_response(resp.json())
