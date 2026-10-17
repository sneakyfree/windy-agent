"""list_my_agents + message_agent: agent teams v1, same names and shapes as the Chat roster.

Contract: windy-contracts team-tools.v1. Plain verbs; the model decides who to ask and what to say.
TRUSTED band = the owner and the owner's own agents (siblings); a stranger agent cannot use them.
Dark: needs WINDY_TEAMS=1.
"""

from __future__ import annotations

from typing import Any

from windyfly.agent import teams
from windyfly.agent.capabilities.descriptor import Band, Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.channels import silence, teamwork


def register_teams_capabilities(registry: CapabilityRegistry, config: dict[str, Any] | None = None) -> None:
    if not silence.enabled():
        return

    registry.register(Capability(
        id="list_my_agents", name="List my agents",
        description="List the agents that belong to your owner (including you).",
        handler=lambda: teams.list_my_agents(),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        tier=Tier.READ_EXTERNAL, band_required=Band.TRUSTED,
    ))
    registry.register(Capability(
        id="message_agent", name="Message an agent",
        description="Send a message to another of your owner's agents. They answer in an ordinary message.",
        handler=lambda *, to, text: teams.message_agent(to, text),
        input_schema={
            "type": "object",
            "properties": {
                "to": {"type": "string", "maxLength": 200,
                       "description": "Name, passport or matrix id of one of your owner's agents."},
                "text": {"type": "string", "minLength": 1, "maxLength": 4000},
            },
            "required": ["to", "text"], "additionalProperties": False,
        },
        tier=Tier.EXTERNAL_EFFECT, band_required=Band.TRUSTED, audit_required=True,
    ))
    if not teamwork.enabled():
        return
    # agent-teamwork.v1: one typed request; the result comes back later. OWNER only: a turn another agent
    # triggered (TRUSTED at most) can't hand out work, so no chain grows from an agent's message.
    registry.register(Capability(
        id="ask_agent", name="Ask one of my agents",
        description=("Give one of your owner's agents in this room one task, as a typed request. It answers "
                     "once, later; when every agent you asked has answered you get all the answers together "
                     "and write the owner ONE answer. Use it when another agent is better at a part of the "
                     "owner's request."),
        handler=lambda *, to, ask, deliverable="", turns=None, seconds=None: teamwork.ask_agent(
            to, ask, deliverable, turns, seconds),
        input_schema={
            "type": "object",
            "properties": {
                "to": {"type": "string", "maxLength": 255,
                       "description": "The agent's name or Matrix id, one of the owner's agents in this room."},
                "ask": {"type": "string", "minLength": 1, "maxLength": 2000,
                        "description": "What to do, in plain words, with everything the agent needs."},
                "deliverable": {"type": "string", "maxLength": 500,
                                "description": "What the answer must contain, in one line."},
                "turns": {"type": "integer", "minimum": 1, "maximum": 20},
                "seconds": {"type": "integer", "minimum": 10, "maximum": 1800},
            },
            "required": ["to", "ask"], "additionalProperties": False,
        },
        tier=Tier.EXTERNAL_EFFECT, band_required=Band.OWNER, audit_required=True,
    ))
