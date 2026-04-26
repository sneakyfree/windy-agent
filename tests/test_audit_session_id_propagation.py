"""Audit ledger session_id propagation regression test.

Caught by the windy-0 stress harness 2026-04-26: every row in the
``agent_actions`` audit ledger had ``session_id IS NULL``, so
correlating "what tools did request X invoke?" was impossible. The
Wave 14 audit hooks already accepted a ``session_id_provider``
callable but the production install site never passed one.

This test pins three things:

1. ``set_current_session_id`` populates a contextvar.
2. The default ``session_id_provider`` reads that contextvar.
3. ``install_audit_hooks`` defaults to that provider when the caller
   doesn't supply one (the real-world install path that was broken).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from windyfly.agent.capabilities import (
    Band,
    Capability,
    CapabilityRegistry,
    install_audit_hooks,
    set_current_session_id,
)
from windyfly.agent.capabilities.audit import get_current_session_id
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


def test_get_current_session_id_starts_none() -> None:
    """Default state of the contextvar is None — anyone reading without
    a prior set sees no session attribution."""
    set_current_session_id(None)
    assert get_current_session_id() is None


def test_set_then_get_round_trips() -> None:
    set_current_session_id("session-abc-123")
    try:
        assert get_current_session_id() == "session-abc-123"
    finally:
        set_current_session_id(None)


def test_install_audit_hooks_default_provider_uses_contextvar() -> None:
    """Bug #8 regression: when the install site doesn't pass
    ``session_id_provider`` (which production didn't), the default
    must fall back to the contextvar instead of producing NULL rows.
    """
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "audit.db"))
        wq = WriteQueue()
        wq.start()
        try:
            registry = CapabilityRegistry()
            registry.register(Capability(
                id="test.echo",
                description="echo for audit test",
                handler=lambda msg="hi": {"ok": True, "msg": msg},
                input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
                audit_required=True,
            ))
            # Production install path — no explicit session_id_provider.
            install_audit_hooks(registry, db, wq)
            # Stamp a session_id like agent_respond does.
            set_current_session_id("test-session-ZZZ")
            try:
                registry.invoke_sync("test.echo", {"msg": "hello"}, Band.OWNER)
            finally:
                set_current_session_id(None)
            wq.stop()  # flush
            row = db.execute(
                "SELECT capability_id, session_id, success "
                "FROM agent_actions WHERE capability_id='test.echo' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            assert row is not None, "no audit row written"
            cap, sid, ok = row
            assert cap == "test.echo"
            assert sid == "test-session-ZZZ", (
                f"session_id was {sid!r} — Bug #8 regression: "
                "audit row has NULL session_id"
            )
            assert ok == 1
        finally:
            db.close()


def test_install_audit_hooks_explicit_provider_overrides_default() -> None:
    """Tests that the existing override path still works (back-compat)."""
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "audit2.db"))
        wq = WriteQueue()
        wq.start()
        try:
            registry = CapabilityRegistry()
            registry.register(Capability(
                id="test.echo2",
                description="echo for audit test 2",
                handler=lambda msg="hi": {"ok": True, "msg": msg},
                input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
                audit_required=True,
            ))
            # Explicit provider — should win over the contextvar.
            install_audit_hooks(
                registry, db, wq,
                session_id_provider=lambda: "explicit-AAA",
            )
            set_current_session_id("contextvar-BBB")
            try:
                registry.invoke_sync("test.echo2", {"msg": "hi"}, Band.OWNER)
            finally:
                set_current_session_id(None)
            wq.stop()
            row = db.execute(
                "SELECT session_id FROM agent_actions WHERE capability_id='test.echo2' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            assert row is not None
            assert row[0] == "explicit-AAA", (
                "explicit session_id_provider should win over contextvar"
            )
        finally:
            db.close()
