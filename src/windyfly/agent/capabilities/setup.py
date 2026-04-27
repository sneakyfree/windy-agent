"""setup.* capability — LLM-callable introspection of which integrations
are connected.

Wave 4-and-a-half / grandma-mode Tier 1: gives the LLM a single tool
call to find out what's dormant, instead of having to discover by
trying each capability and getting a refusal.

Use cases:
  - At conversation start ("let me check what's connected") — the LLM
    can proactively offer to set things up before the user asks.
  - When the user asks "what can you do" — the LLM can give an
    accurate, current answer instead of hallucinating capabilities
    that aren't actually wired.
  - When a user says "set up email" — the LLM can verify it's
    actually dormant before walking through setup.

Tier ``READ_EXTERNAL`` (USER+ band; audited; matches the rest of the
read-side surface). The status data contains no secrets — it only
reports presence/absence of credentials, integration names, and
human-readable setup hints.
"""

from __future__ import annotations

import logging
from typing import Any

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.agent.setup_status import get_setup_status

logger = logging.getLogger(__name__)


def register_setup_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register the ``setup.status`` capability."""
    logger.info("Registering setup.* capabilities (introspection only)")

    def setup_status() -> dict[str, Any]:
        return get_setup_status()

    registry.register(Capability(
        id="setup.status",
        description=(
            "Check which optional integrations (Gmail, Cloudflare, "
            "Calendar, GitHub) are connected. Returns a snapshot of "
            "configured + dormant integrations with friendly setup "
            "hints. Call this at conversation start or when the user "
            "asks 'what can you do?' so you can give an accurate "
            "answer. When an integration is dormant, the response "
            "tells you the chat-friendly setup intent the user can "
            "say (e.g. 'set up email') — prefer that over telling "
            "them to run terminal commands."
        ),
        handler=setup_status,
        tier=Tier.READ_EXTERNAL,
        scope="introspection",
        audit_required=True,
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ))
