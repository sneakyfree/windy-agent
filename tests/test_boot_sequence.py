"""Tests for the Wave 14 BootSequence abstraction.

Covers ordering, dependency checks, optional vs required failure
handling, and the canonical default sequence shape.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from windyfly.agent.boot import (
    BootContext,
    BootDependencyError,
    BootError,
    BootSequence,
    Step,
    default_capability_registration_sequence,
)


def _ctx() -> BootContext:
    return BootContext(
        config={},
        db=MagicMock(),
        write_queue=MagicMock(),
        tool_registry=MagicMock(),
        capability_registry=MagicMock(),
    )


# ── Ordering & happy path ────────────────────────────────────────────


class TestOrdering:
    def test_steps_run_in_declared_order(self):
        order = []
        steps = [
            Step("first",  lambda c: order.append("first")),
            Step("second", lambda c: order.append("second")),
            Step("third",  lambda c: order.append("third")),
        ]
        BootSequence(steps).run(_ctx())
        assert order == ["first", "second", "third"]

    def test_summary_reports_completed_steps(self):
        steps = [
            Step("a", lambda c: None),
            Step("b", lambda c: None),
        ]
        result = BootSequence(steps).run(_ctx())
        assert result["completed"] == ["a", "b"]
        assert result["skipped"] == []
        assert result["failed"] == []
        assert "total_ms" in result

    def test_steps_receive_context(self):
        captured = []
        ctx = _ctx()
        ctx.state["seed"] = 42
        steps = [
            Step("read", lambda c: captured.append(c.state["seed"])),
        ]
        BootSequence(steps).run(ctx)
        assert captured == [42]

    def test_step_can_mutate_context(self):
        steps = [
            Step("write", lambda c: c.state.update({"loaded": True})),
            Step("read",  lambda c: c.state.update({"verified": c.state["loaded"]})),
        ]
        ctx = _ctx()
        BootSequence(steps).run(ctx)
        assert ctx.state == {"loaded": True, "verified": True}


# ── Failure handling ─────────────────────────────────────────────────


class TestFailures:
    def test_required_step_failure_aborts_with_BootError(self):
        def boom(c):
            raise RuntimeError("explosion")

        steps = [
            Step("good", lambda c: None),
            Step("bad",  boom),
            Step("never", lambda c: pytest.fail("should not run")),
        ]
        with pytest.raises(BootError) as ei:
            BootSequence(steps).run(_ctx())
        assert "bad" in str(ei.value)
        assert "explosion" in str(ei.value)

    def test_optional_step_failure_is_skipped_and_continues(self):
        order = []

        def boom(c):
            raise RuntimeError("nope")

        steps = [
            Step("a", lambda c: order.append("a")),
            Step("b", boom, optional=True),
            Step("c", lambda c: order.append("c")),
        ]
        result = BootSequence(steps).run(_ctx())
        assert order == ["a", "c"]
        assert result["completed"] == ["a", "c"]
        assert result["skipped"] == ["b"]
        assert ("b", "nope") in [(n, m) for n, m in result["failed"]]


# ── Dependency checks ────────────────────────────────────────────────


class TestDependencies:
    def test_satisfied_dependency_passes(self):
        order = []
        steps = [
            Step("first",  lambda c: order.append("first")),
            Step("second", lambda c: order.append("second"), requires=("first",)),
        ]
        BootSequence(steps).run(_ctx())
        assert order == ["first", "second"]

    def test_missing_dependency_raises_BootDependencyError(self):
        steps = [
            Step("orphan", lambda c: None, requires=("missing",)),
        ]
        with pytest.raises(BootDependencyError) as ei:
            BootSequence(steps).run(_ctx())
        assert "orphan" in str(ei.value)
        assert "missing" in str(ei.value)

    def test_dependency_on_failed_optional_step_raises(self):
        # An optional step that fails is NOT in the completed list, so
        # later steps depending on it should fail loudly rather than
        # silently running with a missing prerequisite.
        def boom(c):
            raise RuntimeError("nope")

        steps = [
            Step("opt", boom, optional=True),
            Step("dep", lambda c: None, requires=("opt",)),
        ]
        with pytest.raises(BootDependencyError):
            BootSequence(steps).run(_ctx())


# ── The canonical sequence ───────────────────────────────────────────


class TestDefaultSequence:
    def test_returns_a_non_empty_list(self):
        steps = default_capability_registration_sequence()
        assert isinstance(steps, list)
        assert len(steps) > 0

    def test_step_names_unique(self):
        steps = default_capability_registration_sequence()
        names = [s.name for s in steps]
        assert len(names) == len(set(names))

    def test_dependencies_only_reference_earlier_steps(self):
        # Every Step's `requires` must name a step that appears earlier
        # in the sequence (otherwise BootDependencyError on every boot).
        steps = default_capability_registration_sequence()
        seen: set[str] = set()
        for step in steps:
            for dep in step.requires:
                assert dep in seen, (
                    f"step {step.name!r} requires {dep!r} which doesn't "
                    f"appear earlier in the sequence"
                )
            seen.add(step.name)

    def test_capability_audit_runs_before_capability_handlers(self):
        # The Capability Plane needs audit hooks installed before any
        # handler registers, so each handler's first invocation lands
        # in the agent_actions ledger.
        steps = default_capability_registration_sequence()
        names = [s.name for s in steps]
        audit_idx = names.index("capabilities.audit")
        for handler in ("capabilities.filesystem", "capabilities.shell",
                        "capabilities.collaborators"):
            assert names.index(handler) > audit_idx

    def test_reminder_checker_is_optional(self):
        # The background reminder thread shouldn't kill boot if it
        # fails to start (memory/disk pressure, etc.).
        steps = default_capability_registration_sequence()
        for step in steps:
            if step.name == "tools.reminder_checker":
                assert step.optional is True
                return
        pytest.fail("tools.reminder_checker step missing from sequence")
