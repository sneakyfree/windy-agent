"""Capability descriptor — the metadata every tool must declare.

A ``Capability`` bundles a callable handler with the policy fields that
let the application gate it: which band can call it, where it runs,
how reversible it is, what its blast radius looks like.

Tier is the LLM-friendly summary (0–5). Tier sets sensible defaults for
band/sandbox/reversibility/audit; explicit fields on the Capability
override those defaults.

Wave 2 #1 ships the dataclass + enums + tier defaults. The audit hook
is a future seam (callbacks list on the registry — see registry.py)
but no audit storage lands until Wave 2 #2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable

# A handler can be sync or async; the registry's invoke() adapts.
Handler = Callable[..., Any] | Callable[..., Awaitable[Any]]


class Band(IntEnum):
    """Passport-band hierarchy. Higher = more privilege.

    Aligns with the existing ``trust/gate.py`` band names. The IntEnum
    ordering means ``session_band >= cap.band_required`` is the gate
    check — no string comparison gymnastics.
    """

    SANDBOX = 0   # Unknown sender, demo, normie pre-pairing
    USER = 1      # Verified user with passport (grandma after pairing)
    TRUSTED = 2   # Power user (Grant after device pairing)
    OWNER = 3     # Instance owner — the human who set the agent up


class SandboxTier(str):
    """Where the action runs. String-based so future tiers (Modal,
    Daytona, Singularity) can slot in without an enum migration."""

    NONE = "none"                    # Pure compute, no sandbox needed
    HOST_READONLY = "host_readonly"  # Host process, read-only FS
    HOST_RW = "host_rw"              # Host process, read/write
    DOCKER = "docker"                # Docker container
    REMOTE = "remote"                # Remote VM (Modal/Daytona/etc.)


class Reversibility(str):
    """How recoverable the action is if it goes wrong."""

    READ = "read"                              # No change
    WRITE_RECOVERABLE = "write_recoverable"    # Has undo (e.g., move)
    WRITE_DESTRUCTIVE = "write_destructive"    # No undo (e.g., delete)
    EXTERNAL_EFFECT = "external_effect"        # Off-machine (email send, git push)


class Tier(IntEnum):
    """LLM-friendly summary of capability risk class.

    The capability author can pick a tier and let the registry fill in
    sensible defaults for band/sandbox/reversibility/audit. Explicit
    fields on ``Capability`` always override the tier defaults — the
    tier is a *starting point*, not a constraint.
    """

    PURE_COMPUTE = 0       # math, dice, calc, translate
    READ_EXTERNAL = 1      # web search, read_file, list_dir
    WRITE_LOCAL_SAFE = 2   # write_file (new), draft_email
    WRITE_DESTRUCTIVE = 3  # delete, move, git commit (with undo log)
    EXTERNAL_EFFECT = 4    # send email, post message, git push
    FULL_MACHINE = 5       # shell exec, install pkg, modify system


def defaults_for_tier(tier: Tier) -> dict[str, Any]:
    """Recommended defaults for each tier. Capability fields override."""
    return {
        Tier.PURE_COMPUTE: {
            "band_required": Band.SANDBOX,
            "sandbox_tier": SandboxTier.NONE,
            "reversibility": Reversibility.READ,
            "audit_required": False,
            "cost_class": "free",
            "dry_run_supported": False,
            "undo_supported": False,
        },
        Tier.READ_EXTERNAL: {
            "band_required": Band.USER,
            "sandbox_tier": SandboxTier.HOST_READONLY,
            "reversibility": Reversibility.READ,
            "audit_required": True,
            "cost_class": "cheap",
            "dry_run_supported": False,
            "undo_supported": False,
        },
        Tier.WRITE_LOCAL_SAFE: {
            "band_required": Band.USER,
            "sandbox_tier": SandboxTier.HOST_RW,
            "reversibility": Reversibility.WRITE_RECOVERABLE,
            "audit_required": True,
            "cost_class": "cheap",
            "dry_run_supported": True,
            "undo_supported": False,
        },
        Tier.WRITE_DESTRUCTIVE: {
            "band_required": Band.TRUSTED,
            "sandbox_tier": SandboxTier.HOST_RW,
            "reversibility": Reversibility.WRITE_DESTRUCTIVE,
            "audit_required": True,
            "cost_class": "mid",
            "dry_run_supported": True,
            "undo_supported": True,
        },
        Tier.EXTERNAL_EFFECT: {
            "band_required": Band.TRUSTED,
            "sandbox_tier": SandboxTier.HOST_RW,
            "reversibility": Reversibility.EXTERNAL_EFFECT,
            "audit_required": True,
            "cost_class": "mid",
            "dry_run_supported": True,
            "undo_supported": False,
        },
        Tier.FULL_MACHINE: {
            "band_required": Band.TRUSTED,
            "sandbox_tier": SandboxTier.DOCKER,
            "reversibility": Reversibility.WRITE_DESTRUCTIVE,
            "audit_required": True,
            "cost_class": "expensive",
            "dry_run_supported": False,
            "undo_supported": False,
        },
    }[tier]


class CapabilityDenied(Exception):
    """Raised when a session's band is below a capability's requirement.

    Caught by the agent loop's exception path and routed through the
    typed-error classifier (#50) so the user sees a friendly message
    instead of a stack trace. The classifier currently maps
    CapabilityDenied to UNKNOWN — Wave 2 #2 will add a CAPABILITY_DENIED
    category once we know what the user-facing message should be.
    """


@dataclass(frozen=True)
class Capability:
    """A registered tool with its policy metadata.

    The ``id`` is the canonical identifier the LLM uses to call the
    capability. ``description`` and ``input_schema`` form the JSON
    Schema the LLM sees so it knows when and how to invoke.

    All policy fields have a sensible default derived from ``tier`` if
    not specified. To register a capability:

        cap = Capability(
            id="fs.read_file",
            description="Read the contents of a file at the given path.",
            input_schema={"type": "object", "properties": {...}},
            handler=read_file_handler,
            tier=Tier.READ_EXTERNAL,
        )
        registry.register(cap)
    """

    # Identity
    id: str
    description: str
    handler: Handler
    input_schema: dict[str, Any] = field(default_factory=dict)
    name: str = ""  # Human display name; defaults to id

    # Policy
    tier: Tier = Tier.PURE_COMPUTE
    band_required: Band | None = None
    sandbox_tier: str | None = None
    reversibility: str | None = None
    audit_required: bool | None = None
    cost_class: str | None = None
    dry_run_supported: bool | None = None
    undo_supported: bool | None = None

    # Optional rate limit spec (e.g., "100/hour"). Enforcement is a
    # future PR — this just records the author's intent for now.
    rate_limit: str | None = None

    # Free-form scope description for now. Will formalize in Wave 2 #2
    # alongside the audit table.
    scope: str = ""

    def resolved(self) -> "Capability":
        """Return a copy with tier-default fallbacks filled in.

        Frozen dataclasses can't be mutated, so we build a new instance
        with the defaults merged in for any fields the caller left as
        None. The registry calls this on register() so downstream code
        always sees a fully-populated descriptor.
        """
        defaults = defaults_for_tier(self.tier)
        merged: dict[str, Any] = {}
        for key in (
            "band_required", "sandbox_tier", "reversibility",
            "audit_required", "cost_class", "dry_run_supported",
            "undo_supported",
        ):
            current = getattr(self, key)
            merged[key] = current if current is not None else defaults[key]
        return Capability(
            id=self.id,
            description=self.description,
            handler=self.handler,
            input_schema=self.input_schema,
            name=self.name or self.id,
            tier=self.tier,
            rate_limit=self.rate_limit,
            scope=self.scope,
            **merged,
        )
