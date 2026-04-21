"""P4 chaos resilience tests — bot hardening sprint.

Specifically the failure modes the synthetic harness can't catch:
  - Concurrent agent_respond calls racing on the WriteQueue / audit
  - Mock provider 5xx producing a clean typed-error to the user
    (not generic "Sorry")
  - shell.exec when Docker daemon is unavailable returning a clean
    actionable error rather than a Python stack trace

The live launchd-respawn test is NOT here (pytest can't easily
manipulate launchd). It's in the wake-up report as a manual
verification step the operator runs.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from windyfly.agent.capabilities import (
    Band,
    CapabilityRegistry,
)
from windyfly.agent.capabilities.shell import (
    _shell_exec_handler,
    register_shell_capabilities,
)
from windyfly.channels.errors import classify, ErrorCategory
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


@pytest.fixture
def db_and_wq():
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "chaos.db"))
        wq = WriteQueue()
        wq.start()
        try:
            yield db, wq
        finally:
            wq.stop()
            db.close()


@pytest.fixture
def fresh_registry():
    """Yield a fresh CapabilityRegistry, swap it in, restore after."""
    from windyfly.agent import capabilities as caps_pkg
    from windyfly.agent import loop as loop_module
    original_pkg = caps_pkg.capability_registry
    original_loop = loop_module.capability_registry
    fresh = CapabilityRegistry()
    caps_pkg.capability_registry = fresh
    loop_module.capability_registry = fresh
    try:
        yield fresh
    finally:
        caps_pkg.capability_registry = original_pkg
        loop_module.capability_registry = original_loop


def _drain(wq: WriteQueue, timeout: float = 4.0) -> None:
    start = time.time()
    while not wq._queue.empty():
        if time.time() - start > timeout:
            raise TimeoutError("write queue did not drain")
        time.sleep(0.05)
    time.sleep(0.2)


# ── Chaos 1: concurrent agent_respond on shared DB + WriteQueue ────


def test_chaos_concurrent_agent_respond(db_and_wq, fresh_registry):
    """10 messages dispatched in parallel should produce 10 episode
    pairs, no DB-locked errors, no audit corruption."""
    db, wq = db_and_wq

    def fake_call_llm(messages, **kwargs):
        time.sleep(0.05)  # simulate LLM latency
        return {
            "content": f"reply-to-{messages[-1]['content'][:20]}",
            "model": "test",
            "input_tokens": 1, "output_tokens": 1, "tool_calls": None,
        }

    config = {
        "memory": {"db_path": db.db_path},
        "agent": {"default_model": "glm-4.7"},
        "costs": {"daily_budget_usd": 5.0, "monthly_budget_usd": 50.0},
    }

    from windyfly.agent.loop import agent_respond

    def call_one(i: int):
        return agent_respond(
            config, db, wq,
            f"message {i}", session_id=f"chaos-{i}",
        )

    import concurrent.futures
    with patch("windyfly.agent.loop.call_llm", side_effect=fake_call_llm):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(call_one, i) for i in range(10)]
            results = [f.result(timeout=15) for f in futures]

    assert len(results) == 10
    assert all(isinstance(r, str) for r in results)

    _drain(wq, timeout=8.0)
    # Each session should have produced episodes
    for i in range(10):
        rows = db.fetchall(
            "SELECT * FROM episodes WHERE session_id = ?", (f"chaos-{i}",),
        )
        assert len(rows) >= 1, f"session chaos-{i} got no episodes"


# ── Chaos 2: provider 5xx → clean typed error to user ──────────────


def test_chaos_provider_503_routes_to_typed_error():
    """End-to-end: a 503 from the LLM should produce
    LLM_FAILURE classification with a friendly message."""
    err = RuntimeError(
        "LLM call failed across all providers in chain "
        "(attempted=['zai(glm-4.7)']): 503 Service Unavailable"
    )
    classified = classify(err)
    assert classified.category == ErrorCategory.LLM_FAILURE
    assert "AI service" in classified.user_message
    assert "Traceback" not in classified.user_message
    assert "503" not in classified.user_message  # don't leak HTTP codes


def test_chaos_provider_429_routes_to_rate_limit_message():
    err = RuntimeError("429 Too Many Requests")
    classified = classify(err)
    assert classified.category == ErrorCategory.LLM_RATE_LIMIT
    assert "slow" in classified.user_message.lower()


def test_chaos_provider_failover_chain_exhausted_message_clean():
    """The exact message shape PR #46 raises when every chain
    member fails."""
    err = RuntimeError(
        "LLM call failed across all providers in chain "
        "(attempted=['zai(glm-4.7)', 'openai(gpt-4o-mini)']): 500"
    )
    classified = classify(err)
    assert classified.category == ErrorCategory.LLM_FAILURE
    # The user shouldn't see "attempted=[...]" — that's operator log shape
    assert "attempted=" not in classified.user_message


# ── Chaos 3: shell.exec with Docker stopped → actionable error ─────


def test_chaos_shell_docker_unavailable_clean_error(fresh_registry):
    """When Docker isn't running, shell.exec should raise a clean
    RuntimeError that the typed-error classifier maps to UNKNOWN
    with a friendly message."""
    from windyfly.agent.capabilities.sandbox import DockerNotAvailable

    class _DownDispatcher:
        image = "alpine:3.19"

        def is_available(self):
            return False

        def run(self, command, **kwargs):
            raise DockerNotAvailable("docker daemon not reachable")

    with pytest.raises(RuntimeError, match="requires Docker"):
        _shell_exec_handler(
            command="ls",
            _dispatcher=_DownDispatcher(),
            _band=Band.OWNER,
        )

    # And the message should be actionable
    try:
        _shell_exec_handler(
            command="ls",
            _dispatcher=_DownDispatcher(),
            _band=Band.OWNER,
        )
    except RuntimeError as e:
        assert "Docker Desktop" in str(e) or "host_rw" in str(e)


def test_chaos_shell_docker_unavailable_classifier_path():
    """The classifier should not crash on the Docker-unavailable
    error, even if it falls into UNKNOWN (acceptable for now)."""
    err = RuntimeError(
        "shell.exec requires Docker but it's not available: "
        "docker daemon not reachable"
    )
    classified = classify(err)
    # Acceptable: UNKNOWN with the underlying RuntimeError name in the
    # diagnostic suffix
    assert classified.user_message  # non-empty
    assert "Traceback" not in classified.user_message


# ── Chaos 4: capability raises mid-tool-call ───────────────────────


def test_chaos_capability_raises_during_invoke(db_and_wq, fresh_registry):
    """If a capability handler raises after the band check passes,
    invoke() should propagate the exception (the dispatcher catches
    it, the audit hook records the failure)."""
    from windyfly.agent.capabilities import Capability, Tier

    fresh_registry.register(Capability(
        id="test.boom",
        description="raises mid-call",
        handler=lambda: (_ for _ in ()).throw(ValueError("kaboom")),
        input_schema={"type": "object", "properties": {}, "required": []},
        tier=Tier.PURE_COMPUTE,
    ))

    with pytest.raises(ValueError, match="kaboom"):
        asyncio.run(fresh_registry.invoke("test.boom", {}, Band.OWNER))


def test_chaos_capability_raises_audit_records_failure(db_and_wq, fresh_registry):
    """End-to-end through the audit hook: a failing capability call
    must land in agent_actions with success=0 and the error captured."""
    from windyfly.agent.capabilities import Capability, Tier, install_audit_hooks
    from windyfly.memory.agent_actions import get_failed_actions

    db, wq = db_and_wq
    install_audit_hooks(fresh_registry, db, wq)
    fresh_registry.register(Capability(
        id="test.tracked_boom",
        description="raises and audits",
        handler=lambda: (_ for _ in ()).throw(RuntimeError("audited failure")),
        input_schema={"type": "object", "properties": {}, "required": []},
        tier=Tier.READ_EXTERNAL,  # audit_required=True at this tier
    ))

    with pytest.raises(RuntimeError, match="audited failure"):
        asyncio.run(fresh_registry.invoke("test.tracked_boom", {}, Band.OWNER))

    _drain(wq, timeout=4.0)
    rows = get_failed_actions(db)
    assert len(rows) == 1
    assert rows[0]["capability_id"] == "test.tracked_boom"
    assert rows[0]["success"] == 0
    assert "audited failure" in rows[0]["error_message"]
