"""Capability registry — band-gated discovery and dispatch.

Lives alongside (not on top of) ``tools/registry.py``. Existing tools
keep working through the legacy ``ToolRegistry`` until Wave 2 #3
migrates them. New first-class capabilities (filesystem, shell, git,
etc. in Waves 3-5) register here directly.

The two registry methods that matter most:

  - ``tool_schemas_for_band(band)`` — emits OpenAI/Anthropic-style
    tool schemas filtered to capabilities the session's band can
    actually call. Lower-band sessions never even *see* high-tier
    tools in their LLM context. This is the inversion-of-control
    that makes grandma's instance and Grant's instance run the same
    code with the same registry but different exposed surfaces.

  - ``invoke(id, args, band)`` — the gate. Checks band, calls
    pre/post hooks (audit lands in Wave 2 #2), invokes handler.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable

from windyfly.agent.capabilities.descriptor import (
    Band,
    Capability,
    CapabilityDenied,
)

logger = logging.getLogger(__name__)


# Worker event loop for invoke_sync(). Created lazily on first
# sync-from-inside-an-async-context call. Lives on a daemon thread so
# it doesn't block process shutdown. One per process is enough — every
# invocation gets its own task in this loop, so concurrent sync calls
# don't serialize on a single coroutine.
_worker_loop: asyncio.AbstractEventLoop | None = None
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()


def _ensure_worker_loop() -> asyncio.AbstractEventLoop:
    global _worker_loop, _worker_thread
    with _worker_lock:
        if _worker_loop is None:
            loop = asyncio.new_event_loop()
            t = threading.Thread(
                target=loop.run_forever,
                daemon=True,
                name="capability-invoke-sync-worker",
            )
            t.start()
            _worker_loop = loop
            _worker_thread = t
    return _worker_loop


# Audit hook signatures. Both are no-ops on the bare registry; Wave 2 #2
# will register a hook that writes to the agent_actions ledger table.
PreInvokeHook = Callable[[Capability, dict[str, Any], Band], None]
PostInvokeHook = Callable[[Capability, dict[str, Any], Band, Any, Exception | None], None]


class CapabilityRegistry:
    """In-memory capability registry. One instance per agent process.

    Singleton-ish — most callers should use the module-level
    ``capability_registry`` rather than constructing their own. Tests
    are the obvious exception.
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self._pre_invoke_hooks: list[PreInvokeHook] = []
        self._post_invoke_hooks: list[PostInvokeHook] = []

    # ── Registration ────────────────────────────────────────────────

    def register(self, cap: Capability) -> None:
        """Register a capability. Resolves tier defaults on insert.

        Re-registering the same id is allowed and replaces the prior
        entry — that's how a Wave 2 #3 migration can incrementally
        upgrade legacy tools to first-class capabilities without a
        flag-day.
        """
        resolved = cap.resolved()
        if cap.id in self._capabilities:
            logger.info("Capability %s re-registered (replacing prior)", cap.id)
        self._capabilities[cap.id] = resolved

    def unregister(self, capability_id: str) -> bool:
        """Remove a capability by id. Returns True if removed."""
        return self._capabilities.pop(capability_id, None) is not None

    def get(self, capability_id: str) -> Capability | None:
        return self._capabilities.get(capability_id)

    def all(self) -> list[Capability]:
        return list(self._capabilities.values())

    def count(self) -> int:
        return len(self._capabilities)

    # ── Band-aware discovery ────────────────────────────────────────

    def list_for_band(self, band: Band) -> list[Capability]:
        """Capabilities the given band is allowed to call."""
        return [
            cap for cap in self._capabilities.values()
            if band >= cap.band_required
        ]

    def tool_schemas_for_band(self, band: Band) -> list[dict[str, Any]]:
        """Emit OpenAI/Anthropic-style tool schemas filtered by band.

        Lower-band sessions don't even see high-tier capabilities in
        their LLM context — that's the unique architectural property
        no competitor has. Grandma's GLM call sends a smaller tool
        list than Grant's; same code, different band at boot.
        """
        return [self._to_schema(cap) for cap in self.list_for_band(band)]

    @staticmethod
    def _to_schema(cap: Capability) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": cap.id,
                "description": cap.description,
                "parameters": cap.input_schema or {
                    "type": "object",
                    "properties": {},
                },
            },
        }

    # ── Audit hooks ─────────────────────────────────────────────────

    def add_pre_invoke_hook(self, hook: PreInvokeHook) -> None:
        """Register a callback fired before every invoke().

        Wave 2 #2 will install a hook that writes a started_at row
        to the agent_actions ledger.
        """
        self._pre_invoke_hooks.append(hook)

    def add_post_invoke_hook(self, hook: PostInvokeHook) -> None:
        """Register a callback fired after every invoke() (success or
        failure). Wave 2 #2 will install a hook that closes the
        agent_actions row with success/error/cost."""
        self._post_invoke_hooks.append(hook)

    # ── Dispatch ────────────────────────────────────────────────────

    def invoke_sync(
        self,
        capability_id: str,
        args: dict[str, Any],
        band: Band,
        *,
        timeout: float = 60.0,
    ) -> Any:
        """Sync entry point for callers stuck in synchronous code.

        Two cases:
          - No event loop running on this thread: ``asyncio.run`` works.
          - A loop IS running (we're inside a channel/agent loop):
            ``asyncio.run`` would crash, so we schedule the coroutine
            on a worker loop running in a daemon thread and block on
            its result. The worker loop is created lazily on first use.

        The agent loop today (``agent_respond``) is sync; this is the
        surgical path that lets it dispatch async capabilities without
        a full refactor of every channel. The proper async refactor is
        a future PR.
        """
        coro = self.invoke(capability_id, args, band)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop running on this thread — direct path.
            return asyncio.run(coro)
        # We're inside an async context. Schedule on the worker loop.
        worker = _ensure_worker_loop()
        future = asyncio.run_coroutine_threadsafe(coro, worker)
        return future.result(timeout=timeout)

    async def invoke(
        self,
        capability_id: str,
        args: dict[str, Any],
        band: Band,
    ) -> Any:
        """Invoke a capability with band gating.

        Raises:
            KeyError: if no such capability exists
            CapabilityDenied: if the session's band is too low
            Exception: anything the handler raises propagates after
                the post-invoke hooks fire (so the audit row gets
                marked failed)
        """
        cap = self._capabilities.get(capability_id)
        if cap is None:
            raise KeyError(f"unknown capability: {capability_id}")

        if band < cap.band_required:
            raise CapabilityDenied(
                f"capability {capability_id!r} requires band "
                f"{cap.band_required.name}; session is {band.name}"
            )

        for hook in self._pre_invoke_hooks:
            try:
                hook(cap, args, band)
            except Exception as e:
                logger.warning("pre-invoke hook for %s failed: %s", cap.id, e)

        result: Any = None
        error: Exception | None = None
        try:
            result = cap.handler(**args)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as e:
            error = e
            raise
        finally:
            for hook in self._post_invoke_hooks:
                try:
                    hook(cap, args, band, result, error)
                except Exception as e:
                    logger.warning(
                        "post-invoke hook for %s failed: %s", cap.id, e,
                    )


# Module-level singleton. Code that wants to register a capability
# from anywhere in the codebase imports this directly:
#
#     from windyfly.agent.capabilities import capability_registry
#     capability_registry.register(Capability(...))
capability_registry = CapabilityRegistry()
