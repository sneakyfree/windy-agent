"""Arbitrary code execution and credential writes are OWNER-only.

Before this, **nothing in the entire registry required OWNER**. TRUSTED
bought 100% of the surface — ``shell.exec`` and ``setup.save_credential``
included. That was invisible in practice because no code path yet
*issues* a TRUSTED band: ``resolve_band()`` returns OWNER, SANDBOX, or
USER (guest mode) and nothing else. The hole only opens the day
passport-issued TRUSTED sessions ship, which is why it is worth closing
now rather than after.

The doctrine draws the line, not a threat model:

* **#8's second clause — never gate the owner.** "It is HER agent, on
  HER machine, doing HER work." So the test that matters most here is
  ``test_owner_is_never_gated``: OWNER must still reach 100% of the
  registry. A boundary that inconveniences the owner is the wrong
  boundary, no matter how safe.
* **#6 — roll out the red carpet for bots.** So the line is drawn at
  "acts that take over the agent", not at "acts that are risky".
  Deleting a file, setting a DNS record, opening a GitHub issue are all
  the human's WORK and stay at TRUSTED — a credentialed bot is supposed
  to be able to do those. Running arbitrary code on her machine, and
  changing whose accounts the agent acts on, are not work; they are
  ownership.
"""

from __future__ import annotations

import importlib

import pytest

from windyfly.agent.capabilities.descriptor import Band, Tier, defaults_for_tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

# Modules whose registration functions take (registry, config).
_REGISTRARS = [
    ("filesystem", "register_filesystem_capabilities"),
    ("shell", "register_shell_capabilities"),
    ("ssh", "register_ssh_capabilities"),
    ("github", "register_github_capabilities"),
    ("mcp_client", "register_mcp_client_capabilities"),
    ("cloudflare", "register_cloudflare_capabilities"),
    ("setup", "register_setup_capabilities"),
    ("email", "register_email_capabilities"),
    ("windyword", "register_windyword_capabilities"),
    ("health", "register_health_capabilities"),
    ("vision", "register_vision_capabilities"),
]


@pytest.fixture
def registry():
    reg = CapabilityRegistry()
    for mod_name, fn_name in _REGISTRARS:
        try:
            mod = importlib.import_module(
                f"windyfly.agent.capabilities.{mod_name}"
            )
            getattr(mod, fn_name)(reg, {})
        except Exception:
            # A capability module that needs optional deps just doesn't
            # register here; the assertions below are about the ones
            # that did.
            continue
    assert reg.all(), "no capabilities registered — fixture is broken"
    return reg


def test_owner_is_never_gated(registry):
    """Principle #8's second clause, as an executable assertion.

    If this ever fails, the fix is to lower the capability, not to
    raise the owner.
    """
    everything = registry.all()
    owner_sees = registry.list_for_band(Band.OWNER)
    assert len(owner_sees) == len(everything), (
        "OWNER lost access to: "
        + ", ".join(
            sorted({c.id for c in everything} - {c.id for c in owner_sees})
        )
    )


def test_full_machine_tier_requires_owner():
    """Arbitrary code on the human's machine cannot be earned.

    Pinned at the tier table rather than per-capability so a future
    FULL_MACHINE capability inherits the boundary instead of having to
    remember it.
    """
    assert defaults_for_tier(Tier.FULL_MACHINE)["band_required"] is Band.OWNER


def test_shell_exec_requires_owner(registry):
    cap = registry.get("shell.exec")
    assert cap is not None
    assert cap.band_required is Band.OWNER


def test_saving_a_credential_requires_owner(registry):
    """Changing WHOSE accounts the agent acts on is ownership, not work."""
    cap = registry.get("setup.save_credential")
    assert cap is not None
    assert cap.band_required is Band.OWNER


def test_trusted_cannot_reach_code_execution_or_credentials(registry):
    """The actual hole, stated from the attacker's side."""
    reachable = {c.id for c in registry.list_for_band(Band.TRUSTED)}
    for forbidden in ("shell.exec", "setup.save_credential"):
        assert forbidden not in reachable, (
            f"a TRUSTED session can still call {forbidden}"
        )


def test_ssh_to_an_unknown_host_escalates_to_owner(registry, monkeypatch):
    """The escalation that was already written, but was a no-op.

    ``_ssh_runtime_tier_check`` bumps an unrecognised host to
    FULL_MACHINE and its docstring says that means OWNER. It did not:
    FULL_MACHINE resolved to TRUSTED, and ssh.exec's static tier
    (EXTERNAL_EFFECT) was ALSO TRUSTED — so the escalation moved the
    requirement from TRUSTED to TRUSTED and changed nothing.

    Pre-authorized hosts deliberately stay at TRUSTED: the owner put
    them on the allowlist, and #6 says a credentialed bot should be
    able to use them.
    """
    from windyfly.agent.capabilities import ssh as ssh_mod

    monkeypatch.setattr(ssh_mod, "_host_is_allowed", lambda host: False)
    escalated = ssh_mod._ssh_runtime_tier_check({"host": "stranger.example.com"})
    assert escalated is Tier.FULL_MACHINE
    assert defaults_for_tier(escalated)["band_required"] is Band.OWNER

    monkeypatch.setattr(ssh_mod, "_host_is_allowed", lambda host: True)
    assert ssh_mod._ssh_runtime_tier_check({"host": "kit0"}) is None


def test_ordinary_work_stays_reachable_by_trusted(registry):
    """#6: bots are guests, not suspects.

    These are destructive, and they stay at TRUSTED on purpose — they
    are the human's work, scoped by whatever token the owner already
    configured. Over-gating them would be the anti-bot web this
    product exists to be the opposite of.
    """
    reachable = {c.id for c in registry.list_for_band(Band.TRUSTED)}
    for allowed in ("fs.delete_file", "github.put_file"):
        if registry.get(allowed) is not None:
            assert allowed in reachable, f"{allowed} was over-gated"
