"""Pydantic models for the Eternitas bot registry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RegistrationRequest(BaseModel):
    """Request to register a new bot with Eternitas."""

    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(default="Windy Fly personal AI assistant")
    bot_type: str = Field(default="personal_assistant")
    contact_email: str = Field(default="")
    intended_platforms: list[str] = Field(
        default_factory=lambda: ["windy_chat", "windy_mail"]
    )

    # Internal fields (not sent as top-level API fields, used by mock and
    # by the ADR-064 certificate seed below)
    owner_id: str = Field(default="", exclude=True)
    owner_name: str = Field(default="", exclude=True)
    model_id: str = Field(default="", exclude=True)
    hatch_machine_id: str = Field(default="", exclude=True)
    hatch_timezone: str = Field(default="", exclude=True)
    hardware_specs: dict = Field(default_factory=dict, exclude=True)

    def to_api_payload(self) -> dict[str, Any]:
        """Return the payload shape the Eternitas API expects."""
        return {
            "name": self.name,
            "description": self.description,
            "bot_type": self.bot_type,
            "contact_email": self.contact_email,
            "intended_platforms": self.intended_platforms,
            # ADR-064 — registration mints the canonical signed certificate
            # at Eternitas; the seed carries the ceremony detail this lane
            # holds at hatch time so the ONE certificate is the detailed one.
            "certificate": {
                "owner_name": self.owner_name,
                "hatch_timezone": self.hatch_timezone or "UTC",
                "machine_uuid": self.hatch_machine_id,
                "brain_provider": "windyfly",
                "model_id": self.model_id or None,
                "hardware_specs": self.hardware_specs or None,
            },
        }


class EternitasPassport(BaseModel):
    """A verified bot identity issued by Eternitas."""

    passport_id: str = Field(..., description="Unique ID, e.g. ET-00482")
    name: str = ""
    ept_token: str = Field(default="", description="Eternitas-issued JWT")
    api_key: str = Field(default="", description="Live API key, e.g. et_live_XXXXX")
    status: str = Field(default="active", description="active | suspended | revoked")
    trust_score: int = Field(default=70, description="Initial trust score")
    issued_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    credentials: dict[str, Any] = Field(default_factory=dict)
    provisioned_services: dict[str, Any] = Field(
        default_factory=dict,
        description="Services provisioned under this passport: matrix, mail, phone, etc.",
    )
    # ADR-064 — the canonical certificate block registration now returns:
    # {id, certificate_no, passport, signed_at, json_url, pdf_url, qr_url,
    # verify_url}. Empty dict when Eternitas minted no cert (fail-open) or
    # on older servers.
    certificate: dict[str, Any] = Field(default_factory=dict)

    # Keep backward compat aliases
    @property
    def agent_name(self) -> str:
        return self.name

    @property
    def owner_id(self) -> str:
        return self.credentials.get("owner_id", "")

    @property
    def owner_name(self) -> str:
        return self.credentials.get("owner_name", "")

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> EternitasPassport:
        """Parse an Eternitas API response into a passport."""
        return cls(
            passport_id=data.get("passport", data.get("passport_id", "")),
            name=data.get("name", ""),
            ept_token=data.get("ept_token", ""),
            api_key=data.get("api_key", ""),
            status=data.get("status", "active"),
            trust_score=data.get("trust_score", 70),
            certificate=data.get("certificate") or {},
        )


class BotIdentity(BaseModel):
    """Public-facing bot identity for lookup."""

    passport_id: str
    agent_name: str = ""
    owner_id: str = ""
    status: str = "active"
    registered_at: datetime = Field(default_factory=datetime.utcnow)
    services: list[str] = Field(default_factory=list)


class RevocationResult(BaseModel):
    """Result of revoking an Eternitas passport."""

    passport_id: str
    revoked: bool = False
    services_torn_down: list[str] = Field(default_factory=list)
    error: str = ""
