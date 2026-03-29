"""Pydantic models for the Eternitas bot registry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RegistrationRequest(BaseModel):
    """Request to register a new bot with Eternitas."""

    agent_name: str = Field(..., min_length=1, max_length=64)
    owner_id: str = Field(default="")
    owner_name: str = Field(default="")
    model_id: str = Field(default="")
    hatch_machine_id: str = Field(default="")


class EternitasPassport(BaseModel):
    """A verified bot identity issued by Eternitas."""

    passport_id: str = Field(..., description="Unique ID, e.g. ET-00482")
    agent_name: str
    owner_id: str = ""
    owner_name: str = ""
    status: str = Field(default="active", description="active | suspended | revoked")
    issued_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    credentials: dict[str, Any] = Field(default_factory=dict)
    provisioned_services: dict[str, Any] = Field(
        default_factory=dict,
        description="Services provisioned under this passport: matrix, mail, phone, etc.",
    )


class BotIdentity(BaseModel):
    """Public-facing bot identity for lookup."""

    passport_id: str
    agent_name: str
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
