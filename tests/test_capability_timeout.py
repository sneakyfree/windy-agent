"""Per-tool timeout regressions.

Pre-fix, an async capability handler that hung forever (slow web
fetch, deadlocked subprocess, LLM provider that never responds)
blocked the entire conversation until the systemd watchdog killed
the process. Post-fix, ``asyncio.wait_for`` bounds every async
handler to ``cap.timeout_s`` (default 60s) and raises
``CapabilityTimeout`` so the agent loop can surface a friendly
message and let the conversation continue.
"""

from __future__ import annotations

import asyncio

import pytest

from windyfly.agent.capabilities import (
    Band,
    Capability,
    CapabilityRegistry,
    CapabilityTimeout,
    Tier,
)


@pytest.fixture
def fresh_registry():
    return CapabilityRegistry()


@pytest.mark.asyncio
async def test_async_handler_within_timeout_succeeds(fresh_registry):
    async def fast_handler():
        await asyncio.sleep(0.01)
        return "ok"

    fresh_registry.register(Capability(
        id="test.fast",
        description="quick",
        handler=fast_handler,
        tier=Tier.PURE_COMPUTE,
        timeout_s=2.0,
    ))
    result = await fresh_registry.invoke("test.fast", {}, Band.OWNER)
    assert result == "ok"


@pytest.mark.asyncio
async def test_async_handler_exceeding_timeout_raises_capability_timeout(fresh_registry):
    async def slow_handler():
        await asyncio.sleep(5.0)
        return "never"

    fresh_registry.register(Capability(
        id="test.slow",
        description="slow",
        handler=slow_handler,
        tier=Tier.PURE_COMPUTE,
        timeout_s=0.1,  # 100ms cap
    ))
    with pytest.raises(CapabilityTimeout) as exc_info:
        await fresh_registry.invoke("test.slow", {}, Band.OWNER)
    assert "test.slow" in str(exc_info.value)
    assert "0.1" in str(exc_info.value)


@pytest.mark.asyncio
async def test_default_timeout_when_unset(fresh_registry):
    """A capability with timeout_s=None gets the registry default
    (60s). Verify by passing a handler that's slow enough to fail
    a tight test budget but well under the default."""
    async def medium_handler():
        await asyncio.sleep(0.05)
        return "made it"

    fresh_registry.register(Capability(
        id="test.medium",
        description="default-timeout",
        handler=medium_handler,
        tier=Tier.PURE_COMPUTE,
        # timeout_s left as None default
    ))
    result = await fresh_registry.invoke("test.medium", {}, Band.OWNER)
    assert result == "made it"


@pytest.mark.asyncio
async def test_sync_handler_unaffected_by_timeout(fresh_registry):
    """Sync handlers are NOT cancellable from asyncio. We don't try
    to bound them; the comment in the registry explains why. This
    test ensures the timeout path doesn't crash on a sync return."""
    def sync_handler():
        return 42

    fresh_registry.register(Capability(
        id="test.sync",
        description="sync",
        handler=sync_handler,
        tier=Tier.PURE_COMPUTE,
        timeout_s=0.001,  # absurdly tight; sync ignores it
    ))
    result = await fresh_registry.invoke("test.sync", {}, Band.OWNER)
    assert result == 42


@pytest.mark.asyncio
async def test_post_invoke_hook_fires_on_timeout(fresh_registry):
    """Audit hooks must still fire when the handler times out, so
    the audit row gets marked failed instead of stuck mid-flight."""
    async def slow():
        await asyncio.sleep(2.0)

    hook_calls: list[tuple] = []

    def post_hook(cap, args, band, result, error):
        hook_calls.append((cap.id, error.__class__.__name__ if error else None))

    fresh_registry.register(Capability(
        id="test.timeout",
        description="t",
        handler=slow,
        tier=Tier.PURE_COMPUTE,
        timeout_s=0.05,
    ))
    fresh_registry.add_post_invoke_hook(post_hook)
    with pytest.raises(CapabilityTimeout):
        await fresh_registry.invoke("test.timeout", {}, Band.OWNER)
    assert len(hook_calls) == 1
    assert hook_calls[0] == ("test.timeout", "CapabilityTimeout")
