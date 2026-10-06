"""mind.* : the agent reads and changes its own model in Windy Mind (plan v2.1 S18.4).

Native twins of Windy Mind's six agent tools (status, list_models, switch_model, reset_model,
my_usage, why_was_i_paused), calling Mind with the agent's own EPT+agent through
``windyfly.agent.mind_self``. Reading is for any verified user; CHANGING the model is the
owner's word only (Mind also checks the owner's opt-in, ``may_pick``, and notifies the owner).
No money in any answer. Disable with WINDY_MIND_SELF=0.
"""

from __future__ import annotations

from typing import Any

from windyfly.agent import mind_self
from windyfly.agent.capabilities.descriptor import Band, Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

_NONE: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def register_mind_model_capabilities(registry: CapabilityRegistry, config: dict[str, Any] | None = None) -> None:
    if not mind_self.enabled():
        return

    def status() -> dict[str, Any]:
        return mind_self.status()

    def list_models() -> dict[str, Any]:
        return mind_self.list_models()

    def switch_model(*, model: str) -> dict[str, Any]:
        return mind_self.switch_model(model)

    def reset_model() -> dict[str, Any]:
        return mind_self.reset_model()

    def my_usage() -> dict[str, Any]:
        return mind_self.my_usage()

    def why_paused() -> dict[str, Any]:
        return mind_self.why_paused()

    reads = [
        ("mind.status", "Which model powers me", status, _NONE,
         "Which model is powering this agent right now, its backups, and whether it is on, off or paused. "
         "Use when someone asks what model or brain you are using."),
        ("mind.list_models", "Models I may switch to", list_models, _NONE,
         "The models this agent is allowed to pick for itself. Use before switching, or when asked what "
         "models are available."),
        ("mind.my_usage", "My recent usage", my_usage, _NONE,
         "How much this agent has used in the last day: calls and tokens. Never any money."),
        ("mind.why_paused", "Why am I paused", why_paused, _NONE,
         "Why this agent is off or paused, in plain words, or that it is not paused."),
    ]
    for cid, name, fn, schema, desc in reads:
        registry.register(Capability(
            id=cid, name=name, description=desc, handler=fn, input_schema=schema,
            tier=Tier.READ_EXTERNAL, band_required=Band.USER,
        ))

    registry.register(Capability(
        id="mind.switch_model",
        name="Switch my model",
        description=(
            "Switch the model that powers this agent, when the OWNER asks (for example 'switch to Groq' or "
            "'use Claude Haiku'). Pass the words the owner used; I match them against the models I am "
            "allowed to pick. If it is ambiguous, ask which one. If I am not allowed, say so in plain words "
            "and where the owner can allow it. Never claim a switch happened unless the result says ok."
        ),
        handler=switch_model,
        input_schema={
            "type": "object",
            "properties": {"model": {"type": "string", "description": "The model name or the owner's words."}},
            "required": ["model"],
            "additionalProperties": False,
        },
        tier=Tier.EXTERNAL_EFFECT,
        band_required=Band.OWNER,
        audit_required=True,
    ))
    registry.register(Capability(
        id="mind.reset_model",
        name="Go back to my owner's model",
        description="Undo my own model switch and go back to the model my owner set. Owner only.",
        handler=reset_model,
        input_schema=_NONE,
        tier=Tier.EXTERNAL_EFFECT,
        band_required=Band.OWNER,
        audit_required=True,
    ))
