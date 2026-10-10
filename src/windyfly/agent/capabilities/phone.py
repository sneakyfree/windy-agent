"""phone_contacts_search + phone_sms_compose: the owner's phone as the agent's hands.

Contract: windy-contracts phone-tools.v1 (contacts.search, sms.compose). OWNER band only, and the model sees them
only on a turn where the owner's phone is online in the owner DM (channels/phone_tools.filter_tools).
Plain verbs: each asks the phone and returns at once; the phone's answer arrives as a later message.
Dark: needs WINDY_PHONE_TOOLS=1.
"""

from __future__ import annotations

from typing import Any

from windyfly.agent.capabilities.descriptor import Band, Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.channels import phone_tools


def register_phone_capabilities(registry: CapabilityRegistry, config: dict[str, Any] | None = None) -> None:
    if not phone_tools.enabled():
        return

    registry.register(Capability(
        id="phone_contacts_search", name="Search my phone's contacts",
        description="Ask the owner's phone to search its contacts by name. The phone asks the owner before "
                    "sharing; the answer arrives later as a new message.",
        handler=lambda *, query, field="phone", limit=20: phone_tools.contacts_search(
            query=query, field=field, limit=limit),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 100},
                "field": {"type": "string", "enum": ["phone", "email"],
                          "description": "The one channel to return besides the name."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"], "additionalProperties": False,
        },
        tier=Tier.READ_EXTERNAL, band_required=Band.OWNER,
    ))
    registry.register(Capability(
        id="phone_sms_compose", name="Text from my phone",
        description="Ask the owner's phone to text these people. The owner sees the recipients and text and "
                    "taps Send on the phone; the answer (sent / cancelled per person) arrives later as a new "
                    "message. 'sent' means handed to the phone's messaging app, not delivered.",
        handler=lambda *, recipients, body, mode="individual": phone_tools.sms_compose(
            recipients=recipients, body=body, mode=mode),
        input_schema={
            "type": "object",
            "properties": {
                "recipients": {
                    "type": "array", "minItems": 1, "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string", "maxLength": 200},
                                       "phone": {"type": "string", "maxLength": 32}},
                        "required": ["phone"], "additionalProperties": False,
                    },
                },
                "body": {"type": "string", "minLength": 1, "maxLength": 1600},
                "mode": {"type": "string", "enum": ["individual", "group"]},
            },
            "required": ["recipients", "body"], "additionalProperties": False,
        },
        tier=Tier.EXTERNAL_EFFECT, band_required=Band.OWNER, audit_required=True,
    ))
