"""Capability Plane — band-gated, sandbox-aware tool registration.

The Capability Plane is the architectural choke point for everything
the agent can do that touches the world. Every tool — present or
future — registers as a Capability with explicit metadata about who
can call it (band), where it runs (sandbox tier), how reversible it
is, and what its blast radius looks like. This is the slot every
"hand" plugs into in Waves 3-5.

See ``project_windy_fly_architecture.md`` in user memory for the
strategic framing. Wave 2 #1 (this scaffold) just establishes the
shape; Wave 2 #2 adds the action audit ledger; Wave 2 #3 migrates
existing tools.
"""

from windyfly.agent.capabilities.descriptor import (
    Band,
    Capability,
    CapabilityDenied,
    Reversibility,
    SandboxTier,
    Tier,
    defaults_for_tier,
)
from windyfly.agent.capabilities.audit import install_audit_hooks
from windyfly.agent.capabilities.registry import (
    CapabilityRegistry,
    capability_registry,
)

__all__ = [
    "Band",
    "Capability",
    "CapabilityDenied",
    "CapabilityRegistry",
    "Reversibility",
    "SandboxTier",
    "Tier",
    "capability_registry",
    "defaults_for_tier",
    "install_audit_hooks",
]
