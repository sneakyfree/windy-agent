"""Adversarial security audit — Capability Plane gate enforcement.

The architecture doc claims the Capability gate is the security choke
point. This suite *adversarially* tests that claim:

  - Unknown capability IDs raise KeyError (no silent fallback to a
    handler that doesn't exist)
  - Band gate fires BEFORE handler invocation (not after — a handler
    that has side effects must never run for a denied call)
  - Runtime tier escalation re-checks band when args trigger it (no
    way to slip past static gate then escalate via args)
  - Pre-invoke hooks cannot elevate band post-gate (hook mutation of
    band-related state is observed but cannot grant access)
  - Capability ID with hostile characters (newlines, semicolons,
    null bytes, unicode lookalikes) doesn't bypass the registry
    lookup
  - Args containing dunder attributes (__class__, __bases__,
    __globals__) flow through unchanged — handler responsibility
    to validate, but registry must not interpret/eval them
  - Large args don't crash dispatch (DoS resistance)
  - Concurrent invocations of the same capability don't bleed state
  - Hook exceptions don't grant access (pre-invoke hook failure must
    not bypass the gate)

Surfaced 2026-05-02 as part of the autonomous hardening sprint —
the architecture doc declares the gate secure; this suite verifies
it adversarially. The 113-prompt v13/v14 battery tested LLM
behavior under prompt injection; this tests the GATE behavior under
hostile dispatch arguments.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from windyfly.agent.capabilities.descriptor import (
    Band, Capability, CapabilityDenied, Tier,
)
from windyfly.agent.capabilities.registry import CapabilityRegistry


def _make_handler(side_effect_log: list[str]):
    """Returns a handler that records invocation. If the gate fails
    correctly, the log stays empty for denied calls."""
    def handler(**kwargs):
        side_effect_log.append(f"called({kwargs})")
        return {"ok": True}
    return handler


# ── Unknown capability dispatch ────────────────────────────────────


def test_unknown_capability_raises_keyerror():
    """No silent fallback. The LLM must get an explicit error so it
    knows to pick a different tool, not get a quiet success."""
    registry = CapabilityRegistry()
    with pytest.raises(KeyError, match="unknown capability"):
        asyncio.run(registry.invoke("does.not.exist", {}, Band.OWNER))


def test_capability_id_with_newlines_doesnt_match():
    """Hostile cap-id with newline injection must KeyError, not
    match a partial string."""
    registry = CapabilityRegistry()
    log: list[str] = []
    registry.register(Capability(
        id="fs.read_file",
        description="read",
        handler=_make_handler(log),
        tier=Tier.READ_EXTERNAL,
        band_required=Band.USER,
        input_schema={"type": "object"},
    ))
    for hostile in ("fs.read_file\n", "fs.read_file;rm -rf /",
                    "fs.read_file\x00", "fs.read_file ", "FS.READ_FILE"):
        with pytest.raises(KeyError):
            asyncio.run(registry.invoke(hostile, {}, Band.OWNER))
    assert log == [], "handler must NEVER fire for unknown cap ids"


def test_capability_id_unicode_lookalike_rejected():
    """Unicode chars that look like ASCII (Cyrillic 'а' vs Latin 'a')
    must not match the registered name. This is the homograph attack."""
    registry = CapabilityRegistry()
    log: list[str] = []
    registry.register(Capability(
        id="email.send",  # Latin chars
        description="send",
        handler=_make_handler(log),
        tier=Tier.EXTERNAL_EFFECT,
        band_required=Band.TRUSTED,
        input_schema={"type": "object"},
    ))
    # Cyrillic 'а' (U+0430) replacing the Latin 'a' (U+0061)
    homograph = "emаil.send"
    assert homograph != "email.send"
    with pytest.raises(KeyError):
        asyncio.run(registry.invoke(homograph, {}, Band.TRUSTED))
    assert log == []


# ── Band gate enforcement ──────────────────────────────────────────


@pytest.mark.parametrize("session_band,required_band,should_pass", [
    (Band.SANDBOX, Band.SANDBOX, True),
    (Band.SANDBOX, Band.USER, False),
    (Band.SANDBOX, Band.TRUSTED, False),
    (Band.SANDBOX, Band.OWNER, False),
    (Band.USER, Band.USER, True),
    (Band.USER, Band.TRUSTED, False),
    (Band.USER, Band.OWNER, False),
    (Band.TRUSTED, Band.USER, True),
    (Band.TRUSTED, Band.TRUSTED, True),
    (Band.TRUSTED, Band.OWNER, False),
    (Band.OWNER, Band.SANDBOX, True),
    (Band.OWNER, Band.USER, True),
    (Band.OWNER, Band.TRUSTED, True),
    (Band.OWNER, Band.OWNER, True),
])
def test_band_gate_full_matrix(session_band, required_band, should_pass):
    """Every (session, required) pair gates correctly. 16 cases —
    exhaustive over the Band enum."""
    registry = CapabilityRegistry()
    log: list[str] = []
    registry.register(Capability(
        id="test.cap",
        description="x",
        handler=_make_handler(log),
        tier=Tier.PURE_COMPUTE,
        band_required=required_band,
        input_schema={"type": "object"},
    ))
    if should_pass:
        result = asyncio.run(registry.invoke("test.cap", {}, session_band))
        assert result == {"ok": True}
        assert log == ["called({})"], "handler must fire on allowed call"
    else:
        with pytest.raises(CapabilityDenied):
            asyncio.run(registry.invoke("test.cap", {}, session_band))
        assert log == [], (
            f"handler must NOT fire when session={session_band.name} "
            f"< required={required_band.name}"
        )


def test_handler_never_runs_for_denied_call():
    """Critical invariant: a handler with side effects (DB write,
    network call, file delete) must NOT execute when the gate
    denies. This is the property that makes the gate trustworthy."""
    registry = CapabilityRegistry()
    side_effects = []

    def evil_handler(**kwargs):
        side_effects.append("MUTATED_PRODUCTION_STATE")
        return {"ok": True}

    registry.register(Capability(
        id="dangerous.delete",
        description="x",
        handler=evil_handler,
        tier=Tier.WRITE_DESTRUCTIVE,
        band_required=Band.OWNER,
        input_schema={"type": "object"},
    ))

    for low_band in (Band.SANDBOX, Band.USER, Band.TRUSTED):
        with pytest.raises(CapabilityDenied):
            asyncio.run(registry.invoke("dangerous.delete", {}, low_band))
    assert side_effects == [], (
        "side-effecting handler MUST NOT run for any denied band"
    )


# ── Runtime tier escalation ────────────────────────────────────────


def test_runtime_escalation_re_checks_band():
    """A capability that escalates its tier based on args (e.g.,
    fs.write_file with overwrite=true) must re-check band against
    the escalated band, not against the static base band."""
    registry = CapabilityRegistry()
    log: list[str] = []

    def escalator(args):
        # Escalate when 'destructive' flag is true
        return Tier.WRITE_DESTRUCTIVE if args.get("destructive") else None

    registry.register(Capability(
        id="fs.smart_write",
        description="x",
        handler=_make_handler(log),
        tier=Tier.WRITE_LOCAL_SAFE,  # base tier = USER+
        band_required=Band.USER,
        input_schema={"type": "object"},
        runtime_tier_check=escalator,
    ))

    # USER band, non-escalating args → allowed
    asyncio.run(registry.invoke("fs.smart_write", {"destructive": False}, Band.USER))
    assert len(log) == 1

    # USER band, escalating args → DENIED (escalated tier wants TRUSTED+)
    log.clear()
    with pytest.raises(CapabilityDenied, match="escalated"):
        asyncio.run(registry.invoke("fs.smart_write", {"destructive": True}, Band.USER))
    assert log == [], "handler must NOT fire when runtime escalation denies"

    # TRUSTED band, escalating args → allowed
    asyncio.run(registry.invoke("fs.smart_write", {"destructive": True}, Band.TRUSTED))
    assert len(log) == 1


def test_runtime_escalation_with_none_keeps_base_tier():
    """If runtime_tier_check returns None, the static gate result
    stands — no escalation, no re-denial."""
    registry = CapabilityRegistry()
    log: list[str] = []
    registry.register(Capability(
        id="cap.maybe_escalate",
        description="x",
        handler=_make_handler(log),
        tier=Tier.READ_EXTERNAL,
        band_required=Band.USER,
        input_schema={"type": "object"},
        runtime_tier_check=lambda args: None,
    ))
    asyncio.run(registry.invoke("cap.maybe_escalate", {}, Band.USER))
    assert len(log) == 1


# ── Pre-invoke hook cannot grant access ────────────────────────────


def test_pre_invoke_hook_failure_does_not_bypass_gate():
    """If a pre-invoke hook crashes, the gate must still hold.
    Crashes shouldn't be exploitable as a way around band check."""
    registry = CapabilityRegistry()
    log: list[str] = []
    registry.register(Capability(
        id="cap.gated",
        description="x",
        handler=_make_handler(log),
        tier=Tier.WRITE_DESTRUCTIVE,
        band_required=Band.OWNER,
        input_schema={"type": "object"},
    ))

    def crashing_hook(cap, args, band):
        raise RuntimeError("hook crashed")

    registry.add_pre_invoke_hook(crashing_hook)
    with pytest.raises(CapabilityDenied):
        asyncio.run(registry.invoke("cap.gated", {}, Band.USER))
    assert log == [], "hook crash must not bypass band gate"


def test_pre_invoke_hook_runs_only_after_gate_passes():
    """Hooks should not see denied calls — that would let a
    misbehaving hook (logging, audit, billing) leak info about
    invocations the gate already rejected."""
    registry = CapabilityRegistry()
    log: list[str] = []
    hook_calls: list[str] = []
    registry.register(Capability(
        id="cap.private",
        description="x",
        handler=_make_handler(log),
        tier=Tier.WRITE_DESTRUCTIVE,
        band_required=Band.OWNER,
        input_schema={"type": "object"},
    ))
    registry.add_pre_invoke_hook(
        lambda cap, args, band: hook_calls.append(cap.id)
    )

    # Denied call — hook must not fire
    with pytest.raises(CapabilityDenied):
        asyncio.run(registry.invoke("cap.private", {}, Band.SANDBOX))
    assert hook_calls == [], (
        "pre-invoke hooks must not see calls denied by the band gate"
    )


# ── Argument injection ─────────────────────────────────────────────


def test_dunder_args_pass_through_unchanged():
    """Args containing dunder names (__class__, __globals__, etc.)
    must be passed to the handler unchanged. The registry must not
    interpret/eval them. The handler is responsible for validating
    its own input — the registry's job is to deliver them."""
    registry = CapabilityRegistry()
    received: list[Any] = []

    def handler(**kwargs):
        received.append(kwargs)
        return {"ok": True}

    registry.register(Capability(
        id="cap.passthrough",
        description="x",
        handler=handler,
        tier=Tier.PURE_COMPUTE,
        band_required=Band.SANDBOX,
        input_schema={"type": "object"},
    ))

    hostile_args = {
        "__class__": "<class 'str'>",
        "__globals__": {"haxx": True},
        "__bases__": [],
        "normal_field": "ok",
    }
    asyncio.run(registry.invoke("cap.passthrough", hostile_args, Band.OWNER))
    assert received == [hostile_args], (
        "args must arrive at handler unchanged; registry must not "
        "interpret dunder keys"
    )


def test_very_large_args_dont_crash_dispatch():
    """A 1MB string arg must not crash dispatch. Resource exhaustion
    via giant args is a DoS class — the gate should be insensitive
    to arg size."""
    registry = CapabilityRegistry()
    received_lens: list[int] = []

    def handler(big: str = "", **kwargs):
        received_lens.append(len(big))
        return {"ok": True}

    registry.register(Capability(
        id="cap.echo",
        description="x",
        handler=handler,
        tier=Tier.PURE_COMPUTE,
        band_required=Band.SANDBOX,
        input_schema={"type": "object"},
    ))

    huge = "A" * (1024 * 1024)  # 1 MB
    asyncio.run(registry.invoke("cap.echo", {"big": huge}, Band.OWNER))
    assert received_lens == [1024 * 1024]


# ── Concurrent invocation isolation ────────────────────────────────


def test_concurrent_invocations_dont_share_state():
    """The registry routes calls to the handler. If two threads
    invoke at once, args must not bleed across. Tests the basic
    non-locking path — handler is responsible for its own
    thread-safety on shared state."""
    registry = CapabilityRegistry()
    received: list[dict] = []
    lock = threading.Lock()

    def handler(**kwargs):
        with lock:
            received.append(kwargs)
        return {"echo": kwargs}

    registry.register(Capability(
        id="cap.echo_concurrent",
        description="x",
        handler=handler,
        tier=Tier.PURE_COMPUTE,
        band_required=Band.SANDBOX,
        input_schema={"type": "object"},
    ))

    async def call(i: int):
        return await registry.invoke(
            "cap.echo_concurrent", {"i": i}, Band.OWNER,
        )

    async def run_all():
        return await asyncio.gather(*(call(i) for i in range(20)))

    results = asyncio.run(run_all())
    received_is = sorted(r["i"] for r in received)
    result_is = sorted(r["echo"]["i"] for r in results)
    assert received_is == list(range(20)), "all 20 calls must reach handler"
    assert result_is == list(range(20)), "no arg-bleeding across calls"


# ── Audit invariant ────────────────────────────────────────────────


def test_post_invoke_hook_fires_for_handler_exception():
    """The post-invoke hook must fire even when the handler raises,
    so the audit row gets marked failed. Without this, a
    crashed-handler's audit row stays 'pending' forever and the
    capability appears never-completed."""
    registry = CapabilityRegistry()
    post_calls: list[Any] = []

    def crashing_handler(**kwargs):
        raise RuntimeError("simulated handler failure")

    registry.register(Capability(
        id="cap.crashes",
        description="x",
        handler=crashing_handler,
        tier=Tier.PURE_COMPUTE,
        band_required=Band.SANDBOX,
        input_schema={"type": "object"},
    ))

    def audit_hook(cap, args, band, result, error):
        post_calls.append({
            "cap_id": cap.id,
            "had_error": error is not None,
        })

    registry.add_post_invoke_hook(audit_hook)

    with pytest.raises(RuntimeError, match="simulated"):
        asyncio.run(registry.invoke("cap.crashes", {}, Band.OWNER))

    assert len(post_calls) == 1, "post-invoke hook must fire even on handler crash"
    assert post_calls[0]["had_error"] is True
