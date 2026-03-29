"""Eternitas bot registry — verified identity for AI agents."""

from windyfly.eternitas.models import (
    BotIdentity,
    EternitasPassport,
    RegistrationRequest,
    RevocationResult,
)
from windyfly.eternitas.client import EternitasClient
from windyfly.eternitas.mock import MockEternitasClient
from windyfly.eternitas.provision import provision_eternitas, get_eternitas_client

__all__ = [
    "BotIdentity",
    "EternitasClient",
    "EternitasPassport",
    "MockEternitasClient",
    "RegistrationRequest",
    "RevocationResult",
    "get_eternitas_client",
    "provision_eternitas",
]
