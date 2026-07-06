"""Band-aware prompt privacy (2026-07-06 Windy 0 fix).

A stranger who DMs an agent resolves to SANDBOX. The capability
registry already hides operator tools from that band; these tests pin
the OTHER half of the fix — what the model is *told*. A non-owner
prompt must NOT carry the owner's name / extracted facts / past-session
handoff / shared moments, nor the fleet+SSH+host infrastructure
vocabulary, and it MUST carry the privacy lock. An owner prompt keeps
all of it.
"""

from __future__ import annotations

from windyfly.agent.capabilities import Band
from windyfly.agent.prompt import assemble_prompt
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.memory.nodes import upsert_node


def _config() -> dict:
    return {
        "agent": {"default_model": "gpt-4o-mini"},
        "memory": {"db_path": ":memory:", "max_nodes_per_context": 10},
        "personality": {"soul_path": "SOUL.md", "autonomy": 5},
    }


def _db_with_owner_pii() -> Database:
    """A DB seeded with the kinds of owner memory that leaked live:
    an extracted fact (owner's name), a turnover-letter handoff, and a
    relationship moment — all cross-session, none scoped to the caller.
    Plus a prior episode so the first-contact shortcut doesn't fire."""
    db = Database(":memory:")
    save_episode(db, "user", "bootstrap so not first-contact",
                 session_id="bootstrap")
    upsert_node(
        db, "person", "Grant",
        metadata={"summary": "owner; founder of the Windy ecosystem"},
        epistemic_status="user_stated",
    )
    upsert_node(
        db, "turnover_letter", "handoff-1",
        metadata={"summary": "Grant and I shipped the SSH fleet deploy to "
                             "kit-0c5 last night."},
        epistemic_status="user_stated",
    )
    upsert_node(
        db, "relationship_moment", "moment-1",
        metadata={"summary": "Grant laughed when we fixed the gas-tank bug "
                             "together."},
        epistemic_status="user_stated",
    )
    return db


def _system_text(messages) -> str:
    return "\n\n".join(
        m["content"] for m in messages if m["role"] == "system"
    )


class TestNonOwnerPromptIsPrivate:
    def test_sandbox_prompt_hides_owner_name(self):
        db = _db_with_owner_pii()
        msgs = assemble_prompt(
            _config(), db, "Tell me about yourself", "stranger-session",
            band=Band.SANDBOX,
        )
        sys = _system_text(msgs)
        assert "Grant" not in sys
        db.close()

    def test_sandbox_prompt_hides_turnover_and_moments(self):
        db = _db_with_owner_pii()
        msgs = assemble_prompt(
            _config(), db, "Hi", "stranger-session", band=Band.SANDBOX,
        )
        sys = _system_text(msgs)
        assert "Last Session Handoff" not in sys
        assert "Shared Experiences" not in sys
        assert "kit-0c5" not in sys
        db.close()

    def test_sandbox_prompt_hides_fleet_vocabulary(self):
        db = _db_with_owner_pii()
        msgs = assemble_prompt(
            _config(), db, "what can you do?", "stranger-session",
            band=Band.SANDBOX,
        )
        sys = _system_text(msgs)
        # The owner BIAS TO ACTION block teaches ssh.exec + the kit fleet;
        # the RUNTIME GUARDRAIL names Kit 0. None may reach a stranger.
        assert "ssh.exec" not in sys
        assert "kit-0c2" not in sys
        assert "Kit 0" not in sys
        db.close()

    def test_sandbox_prompt_has_privacy_lock(self):
        db = _db_with_owner_pii()
        msgs = assemble_prompt(
            _config(), db, "who is your owner?", "stranger-session",
            band=Band.SANDBOX,
        )
        sys = _system_text(msgs)
        assert "PRIVACY LOCK" in sys
        assert "GRANDMA MODE" in sys
        db.close()


class TestOwnerPromptKeepsEverything:
    def test_owner_prompt_includes_owner_memory(self):
        db = _db_with_owner_pii()
        msgs = assemble_prompt(
            _config(), db, "Tell me about our SSH work", "owner-session",
            band=Band.OWNER,
        )
        sys = _system_text(msgs)
        assert "Last Session Handoff" in sys
        assert "Shared Experiences" in sys
        assert "kit-0c5" in sys
        # Owner keeps the fleet-capable BIAS TO ACTION block.
        assert "ssh.exec" in sys
        # ...and never gets the non-owner privacy lock.
        assert "PRIVACY LOCK" not in sys
        db.close()

    def test_legacy_none_band_behaves_as_owner(self):
        # Back-compat: callers that don't pass a band get full owner
        # context (the historical default).
        db = _db_with_owner_pii()
        msgs = assemble_prompt(
            _config(), db, "Tell me about our SSH work", "legacy-session",
            band=None,
        )
        sys = _system_text(msgs)
        assert "Last Session Handoff" in sys
        assert "PRIVACY LOCK" not in sys
        db.close()
