"""mind.* : the agent reads and changes its own model in Windy Mind (plan v2.1 S18.4).

Three verbs over Windy Mind's six agent routes (status also carries my_usage + why_was_i_paused;
switch_model 'reset' is reset_model), calling Mind with the agent's own EPT+agent through
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
        # One read verb (tool trim, 10-10): the model, backups and state, plus the last day's use and why paused.
        out = dict(mind_self.status())
        out["usage"] = mind_self.my_usage()
        out["paused"] = mind_self.why_paused()
        return out

    def list_models() -> dict[str, Any]:
        return mind_self.list_models()

    def switch_model(*, model: str) -> dict[str, Any]:
        if (model or "").strip().lower() == "reset":
            return mind_self.reset_model()
        return mind_self.switch_model(model)

    reads = [
        ("mind.status", "Which model powers me", status, _NONE,
         "Which model is powering this agent right now, its backups, whether it is on, off or paused and why, "
         "and its last day's calls and tokens (never money)."),
        ("mind.list_models", "Models I may switch to", list_models, _NONE,
         "The models this agent is allowed to pick for itself."),
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
            "Switch the model that powers this agent, owner's word only: an exact model id from "
            "mind.list_models, or 'reset' to go back to the model the owner set. The result says whether it worked."
        ),
        handler=switch_model,
        input_schema={
            "type": "object",
            "properties": {"model": {"type": "string",
                                     "description": "Exact model id from mind.list_models, or 'reset'."}},
            "required": ["model"],
            "additionalProperties": False,
        },
        tier=Tier.EXTERNAL_EFFECT,
        band_required=Band.OWNER,
        audit_required=True,
    ))
