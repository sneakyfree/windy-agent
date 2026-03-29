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


class EternitasClient:
    """HTTP client for the Eternitas bot registry API.

    When the real Eternitas service is deployed, this client talks to it.
    For local development, use MockEternitasClient instead.
    """

    def __init__(self, api_url: str | None = None, service_token: str | None = None) -> None:
        self.api_url = (
            api_url
            or os.environ.get("ETERNITAS_API_URL", "https://api.eternitas.ai")
        ).rstrip("/")
        self.service_token = service_token or os.environ.get("ETERNITAS_SERVICE_TOKEN", "")

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.service_token:
            h["Authorization"] = f"Bearer {self.service_token}"
        return h

    async def register(self, request: RegistrationRequest) -> EternitasPassport:
        """Register a new bot and receive a passport."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.api_url}/api/v1/register",
                json=request.model_dump(),
                headers=self._headers(),
            )
            resp.raise_for_status()
            return EternitasPassport.model_validate(resp.json())

    async def verify(self, passport_id: str) -> EternitasPassport | None:
        """Verify a passport is valid and active."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.api_url}/api/v1/verify/{passport_id}",
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return EternitasPassport.model_validate(resp.json())

    async def lookup(self, agent_name: str) -> BotIdentity | None:
        """Look up a bot's public identity by name."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.api_url}/api/v1/lookup",
                params={"agent_name": agent_name},
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return BotIdentity.model_validate(resp.json())

    async def revoke(self, passport_id: str, reason: str = "") -> RevocationResult:
        """Revoke a passport and trigger cascade teardown of services."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.api_url}/api/v1/revoke/{passport_id}",
                json={"reason": reason},
                headers=self._headers(),
            )
            resp.raise_for_status()
            return RevocationResult.model_validate(resp.json())

    async def update_services(
        self, passport_id: str, services: dict[str, str]
    ) -> EternitasPassport:
        """Update the provisioned services record for a passport."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(
                f"{self.api_url}/api/v1/passport/{passport_id}/services",
                json=services,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return EternitasPassport.model_validate(resp.json())
