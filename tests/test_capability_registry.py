"""Tests for the Capability Plane scaffold (Wave 2 #1).

Covers:
- Tier defaults flow through .resolved()
- Explicit fields override tier defaults
- Band-filtered discovery (the inversion-of-control property)
- LLM tool-schema emission shape
- Pre/post hook firing on invoke
- CapabilityDenied raised when band is insufficient
- Async + sync handler adaptation
"""

from __future__ import annotations

import pytest

from windyfly.agent.capabilities import (
    Band,
    Capability,
    CapabilityDenied,
    CapabilityRegistry,
    Reversibility,
    SandboxTier,
    Tier,
    defaults_for_tier,
)


# ── Descriptor / tier defaults ─────────────────────────────────────


def test_band_int_ordering():
    assert Band.SANDBOX < Band.USER < Band.TRUSTED < Band.OWNER
    # The whole point: int comparison gives us the gate check
    assert Band.OWNER >= Band.USER
    assert not (Band.SANDBOX >= Band.TRUSTED)


def test_tier_defaults_pure_compute_is_open():
    d = defaults_for_tier(Tier.PURE_COMPUTE)
    assert d["band_required"] == Band.SANDBOX
    assert d["sandbox_tier"] == SandboxTier.NONE
    assert d["audit_required"] is False


def test_tier_defaults_full_machine_is_locked_down():
    d = defaults_for_tier(Tier.FULL_MACHINE)
    assert d["band_required"] == Band.TRUSTED
    assert d["sandbox_tier"] == SandboxTier.DOCKER
    assert d["audit_required"] is True


def test_resolved_fills_in_tier_defaults_for_unset_fields():
    cap = Capability(
        id="dice.roll",
        description="Roll a die",
        handler=lambda sides=6: 4,  # chosen by fair die roll
        tier=Tier.PURE_COMPUTE,
    )
    r = cap.resolved()
    assert r.band_required == Band.SANDBOX
    assert r.audit_required is False
    assert r.cost_class == "free"


def test_explicit_fields_override_tier_defaults():
    cap = Capability(
        id="weird.case",
        description="A pure-compute thing that we still want audited",
        handler=lambda: None,
        tier=Tier.PURE_COMPUTE,
        audit_required=True,           # override default False
        band_required=Band.TRUSTED,    # override default SANDBOX
    )
    r = cap.resolved()
    assert r.audit_required is True
    assert r.band_required == Band.TRUSTED
    # Non-overridden fields still take tier defaults
    assert r.sandbox_tier == SandboxTier.NONE


def test_resolved_preserves_id_and_handler():
    h = lambda: "ok"
    cap = Capability(
        id="x.y", description="d", handler=h, tier=Tier.PURE_COMPUTE,
    )
    r = cap.resolved()
    assert r.id == "x.y"
    assert r.handler is h


# ── Registry ────────────────────────────────────────────────────────


def _cap(id, tier=Tier.PURE_COMPUTE, **overrides):
    return Capability(
        id=id,
        description=f"description of {id}",
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "string"}},
        },
        handler=lambda x="default": f"{id}({x})",
        tier=tier,
        **overrides,
    )


def test_register_and_get():
    r = CapabilityRegistry()
    r.register(_cap("a.b"))
    assert r.get("a.b") is not None
    assert r.get("nope") is None
    assert r.count() == 1


def test_re_register_replaces():
    r = CapabilityRegistry()
    r.register(_cap("a.b", tier=Tier.PURE_COMPUTE))
    r.register(_cap("a.b", tier=Tier.READ_EXTERNAL))
    assert r.count() == 1
    assert r.get("a.b").band_required == Band.USER  # READ_EXTERNAL default


def test_unregister():
    r = CapabilityRegistry()
    r.register(_cap("a.b"))
    assert r.unregister("a.b") is True
    assert r.unregister("a.b") is False
    assert r.count() == 0


def test_list_for_band_filters_correctly():
    r = CapabilityRegistry()
    r.register(_cap("dice", tier=Tier.PURE_COMPUTE))      # SANDBOX+
    r.register(_cap("read_url", tier=Tier.READ_EXTERNAL))  # USER+
    r.register(_cap("shell", tier=Tier.FULL_MACHINE))     # TRUSTED+

    assert {c.id for c in r.list_for_band(Band.SANDBOX)} == {"dice"}
    assert {c.id for c in r.list_for_band(Band.USER)} == {"dice", "read_url"}
    assert {c.id for c in r.list_for_band(Band.TRUSTED)} == {"dice", "read_url", "shell"}
    assert {c.id for c in r.list_for_band(Band.OWNER)} == {"dice", "read_url", "shell"}


def test_tool_schemas_for_band_emits_openai_shape():
    r = CapabilityRegistry()
    r.register(_cap("dice"))
    schemas = r.tool_schemas_for_band(Band.SANDBOX)
    assert len(schemas) == 1
    s = schemas[0]
    assert s["type"] == "function"
    assert s["function"]["name"] == "dice"
    assert "description" in s["function"]
    assert s["function"]["parameters"]["type"] == "object"


def test_tool_schemas_filtered_by_band():
    r = CapabilityRegistry()
    r.register(_cap("dice", tier=Tier.PURE_COMPUTE))
    r.register(_cap("shell", tier=Tier.FULL_MACHINE))
    sandbox_schemas = r.tool_schemas_for_band(Band.SANDBOX)
    trusted_schemas = r.tool_schemas_for_band(Band.TRUSTED)
    sandbox_names = {s["function"]["name"] for s in sandbox_schemas}
    trusted_names = {s["function"]["name"] for s in trusted_schemas}
    assert sandbox_names == {"dice"}
    assert trusted_names == {"dice", "shell"}


# ── Invoke + gating ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invoke_calls_sync_handler():
    r = CapabilityRegistry()
    r.register(_cap("dice"))
    out = await r.invoke("dice", {"x": "hi"}, Band.SANDBOX)
    assert out == "dice(hi)"


@pytest.mark.asyncio
async def test_invoke_calls_async_handler():
    r = CapabilityRegistry()

    async def handler(x="default"):
        return f"async({x})"

    r.register(Capability(
        id="async.op",
        description="async handler",
        handler=handler,
        tier=Tier.PURE_COMPUTE,
    ))
    out = await r.invoke("async.op", {"x": "hi"}, Band.SANDBOX)
    assert out == "async(hi)"


@pytest.mark.asyncio
async def test_invoke_missing_capability_raises_keyerror():
    r = CapabilityRegistry()
    with pytest.raises(KeyError):
        await r.invoke("nope", {}, Band.OWNER)


@pytest.mark.asyncio
async def test_invoke_below_band_raises_capability_denied():
    r = CapabilityRegistry()
    r.register(_cap("shell", tier=Tier.FULL_MACHINE))  # TRUSTED+
    with pytest.raises(CapabilityDenied) as excinfo:
        await r.invoke("shell", {}, Band.USER)
    assert "TRUSTED" in str(excinfo.value)
    assert "USER" in str(excinfo.value)


@pytest.mark.asyncio
async def test_invoke_at_exact_band_allowed():
    r = CapabilityRegistry()
    r.register(_cap("readurl", tier=Tier.READ_EXTERNAL))  # USER+
    # USER == required, should succeed
    out = await r.invoke("readurl", {"x": "hi"}, Band.USER)
    assert out == "readurl(hi)"


@pytest.mark.asyncio
async def test_pre_invoke_hook_fires():
    r = CapabilityRegistry()
    seen: list = []
    r.add_pre_invoke_hook(lambda cap, args, band: seen.append((cap.id, args, band)))
    r.register(_cap("dice"))
    await r.invoke("dice", {"x": "hi"}, Band.OWNER)
    assert seen == [("dice", {"x": "hi"}, Band.OWNER)]


@pytest.mark.asyncio
async def test_post_invoke_hook_fires_on_success():
    r = CapabilityRegistry()
    seen: list = []
    r.add_post_invoke_hook(
        lambda cap, args, band, result, error: seen.append(
            (cap.id, result, error)
        )
    )
    r.register(_cap("dice"))
    await r.invoke("dice", {"x": "hi"}, Band.OWNER)
    assert seen == [("dice", "dice(hi)", None)]


@pytest.mark.asyncio
async def test_post_invoke_hook_fires_on_failure():
    r = CapabilityRegistry()
    seen: list = []
    r.add_post_invoke_hook(
        lambda cap, args, band, result, error: seen.append(
            (cap.id, result, type(error).__name__ if error else None)
        )
    )

    def bad():
        raise ValueError("boom")

    r.register(Capability(
        id="bad.op",
        description="raises",
        handler=bad,
        tier=Tier.PURE_COMPUTE,
    ))
    with pytest.raises(ValueError, match="boom"):
        await r.invoke("bad.op", {}, Band.OWNER)
    assert seen == [("bad.op", None, "ValueError")]


def test_module_level_singleton_exists():
    from windyfly.agent.capabilities import capability_registry
    assert isinstance(capability_registry, CapabilityRegistry)
