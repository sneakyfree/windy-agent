"""First-message tour (PR #142) regression tests.

Pin the contract:

  - is_first_contact() returns True only when episodes AND nodes
    are both empty
  - is_first_contact() degrades safely (returns False) on schema
    errors so a broken DB doesn't welcome-loop forever
  - format_welcome() returns text mentioning /reset, /resurrect,
    the slash-/menu, /spend, and voice notes — these are the
    five vocabulary items every grandma should learn first
  - agent_respond on a virgin DB returns the welcome WITHOUT
    calling the LLM (cost saving + works when creds are dead)
  - The user message + welcome reply both land in episodes so
    the next call no longer triggers the welcome
  - Welcome fires AFTER pause/resurrect guards (those higher-
    priority signals win) but BEFORE the LLM dispatch
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# Opt this entire file out of the conftest autouse that suppresses
# the first-contact welcome — these tests specifically verify that
# behavior. See tests/conftest.py for the autouse mechanic.
pytestmark = pytest.mark.virgin_db_welcome

from windyfly.agent.loop import agent_respond
from windyfly.agent.welcome import (
    WELCOME_TEXT,
    format_welcome,
    is_first_contact,
)
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.memory.write_queue import WriteQueue


def _make_config():
    return {
        "agent": {
            "default_model": "claude-haiku-4-5-20251001",
            "max_context_tokens": 8000,
            "max_response_tokens": 2000,
            "temperature": 0.7,
        },
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 20, "max_nodes_per_context": 10},
        "personality": {"soul_path": "SOUL.md", "humor_level": 7, "formality": 4,
                        "proactivity": 5, "verbosity": 5, "reasoning_depth": 6,
                        "autonomy": 3, "epistemic_strictness": 5},
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }


@pytest.fixture
def virgin_db():
    """Empty DB — episodes + nodes both 0 rows."""
    db = Database(":memory:")
    yield db
    db.close()


@pytest.fixture
def stack(virgin_db):
    config = _make_config()
    wq = WriteQueue(); wq.start()
    yield config, virgin_db, wq
    try: wq.stop()
    except Exception: pass


# ── is_first_contact() predicate ──────────────────────────────────


def test_first_contact_true_on_virgin_db(virgin_db):
    assert is_first_contact(virgin_db) is True


def test_first_contact_false_after_episode_saved(virgin_db):
    save_episode(virgin_db, "user", "hello", session_id="s1")
    assert is_first_contact(virgin_db) is False


def test_first_contact_false_after_node_saved(virgin_db):
    """Even if episodes is empty, an extracted fact (node) should
    suppress the welcome — the bot has SOMETHING to anchor on."""
    from windyfly.memory.nodes import upsert_node
    upsert_node(virgin_db, "fact", "user_likes_coffee")
    assert is_first_contact(virgin_db) is False


def test_first_contact_false_on_schema_error():
    """Broken DB → don't welcome-loop forever."""
    class BrokenDB:
        def fetchone(self, *a, **kw):
            raise RuntimeError("DB broken")
    assert is_first_contact(BrokenDB()) is False  # type: ignore[arg-type]


# ── format_welcome() content ──────────────────────────────────────


def test_welcome_mentions_recovery_commands():
    """Pin /reset and /resurrect — the recovery vocabulary that
    appears everywhere else (PR #141 footer, /help, slash menu).
    Consistency = grandma learns once, sees everywhere."""
    text = format_welcome()
    assert "/reset" in text
    assert "/resurrect" in text


def test_welcome_mentions_slash_menu():
    """The tap-/ menu is the discoverability path (PR #139)."""
    text = format_welcome()
    assert "/" in text  # somewhere we mention "tap /"


def test_welcome_mentions_spend():
    """Money worry is universal — surface /spend at hatch time."""
    text = format_welcome()
    assert "/spend" in text


def test_welcome_mentions_voice():
    """Voice ingestion (PR #129) is the killer accessibility feature
    for grandmas who can't type fast — surface it at hatch time."""
    text = format_welcome().lower()
    assert "voice" in text


def test_welcome_under_telegram_message_cap():
    """Single Telegram message cap is 4096."""
    assert len(format_welcome()) <= 4096


def test_welcome_text_is_stable():
    """The constant and the function must agree — pinning catches
    accidental drift if someone edits one but not the other."""
    assert format_welcome() == WELCOME_TEXT


# ── Integration: agent_respond shortcut ───────────────────────────


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_agent_respond_returns_welcome_on_virgin_db(mock_llm, _online, stack):
    """The big one: a fresh-hatched bot's first message gets the
    deterministic welcome WITHOUT burning an LLM call. This is
    cost savings AND survives dead-creds at hatch time."""
    config, db, wq = stack

    response = agent_respond(config, db, wq, "Hello?", "fresh-session")

    assert response == WELCOME_TEXT
    assert mock_llm.call_count == 0, (
        "first contact must NOT call the LLM — welcome is "
        "deterministic and free"
    )


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_welcome_persists_episodes_so_next_message_uses_llm(
    mock_llm, _online, stack,
):
    """After the welcome ships, episodes is no longer empty.
    The user's NEXT message goes through the normal LLM path."""
    config, db, wq = stack

    # First message → welcome, no LLM
    agent_respond(config, db, wq, "Hi!", "fresh-session")
    assert mock_llm.call_count == 0

    # Drain the write queue so the saved episodes are visible
    import time
    time.sleep(0.5)
    rows = db.fetchall("SELECT role, content FROM episodes")
    contents = [r["content"] for r in rows]
    # Both the user's first message AND the welcome should be saved
    assert any("Hi!" in c for c in contents), (
        "user's first message must be saved as episode"
    )
    assert any("Windy Fly" in c and "just hatched" in c for c in contents), (
        "welcome reply must be saved as episode so prompt-assembly "
        "doesn't see it as missing context"
    )

    # Now is_first_contact should return False
    assert is_first_contact(db) is False

    # Second message — episodes is no longer empty, so the LLM
    # actually fires.
    mock_llm.return_value = {
        "content": "Real reply", "model": "claude-haiku-4-5-20251001",
        "input_tokens": 100, "output_tokens": 20, "tool_calls": None,
    }
    agent_respond(config, db, wq, "What's the weather?", "fresh-session")
    assert mock_llm.call_count == 1, (
        "second message should hit the LLM normally; "
        "welcome must be a one-time deterministic shortcut"
    )


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_welcome_does_not_fire_when_paused(mock_llm, _online, stack, monkeypatch, tmp_path):
    """If the bot is paused, the pause message wins — welcome stays
    quiet. Paused-bot signal is higher priority than first-contact
    onboarding."""
    config, db, wq = stack
    # Set up isolated pause flag and pause the bot
    monkeypatch.setenv("WINDY_PAUSE_FLAG", str(tmp_path / ".paused"))
    from windyfly.agent.spend_monitor import pause
    pause(reason="test", actor="test")

    response = agent_respond(config, db, wq, "Hi!", "fresh-session")

    assert "paused" in response.lower(), (
        "paused-bot signal must take priority over welcome"
    )
    assert WELCOME_TEXT not in response
    assert mock_llm.call_count == 0


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_welcome_wins_over_resurrection_on_virgin_db(mock_llm, _online, stack, monkeypatch, tmp_path):
    """Edge case: a brand-new bot with the resurrect flag pre-set.
    The welcome is deterministic, free (no LLM), and gives the
    grandma a consistent first impression — so it wins. On the
    NEXT message (after the welcome populates episodes), resurrect
    takes over and routes through Ollama as expected.

    Why welcome first: every grandma should see the SAME first
    reply regardless of weird states. Resurrection is a recovery
    flow that matters once the bot has history; it's not a hatch-
    time concern."""
    config, db, wq = stack
    monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".resurrected"))
    from windyfly.agent.resurrect import resurrect
    with patch("windyfly.agent.resurrect.list_installed_ollama_models",
               return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}]):
        resurrect(actor="test")

    response = agent_respond(config, db, wq, "Hi!", "fresh-session")

    assert response == WELCOME_TEXT, (
        "on virgin DB, welcome wins over resurrection — first "
        "impression must be deterministic"
    )
    assert mock_llm.call_count == 0


# ── Naming Ceremony: welcome honors the given name ────────────────


def test_welcome_introduces_agent_by_given_name():
    """A helper the owner named "Sunny" must introduce itself as Sunny.

    Pre-fix, the deterministic first-contact tour hardcoded "I'm Windy
    Fly" and ignored config [agent] name entirely — a freshly-named
    agent's very first greeting contradicted its naming ceremony."""
    text = format_welcome({"agent": {"name": "Sunny"}})
    assert "I'm Sunny —" in text
    assert "Windy Fly" not in text
    # The orientation body is unchanged — same recovery vocabulary.
    assert "/reset" in text and "/resurrect" in text and "/spend" in text


def test_welcome_unnamed_config_renders_legacy_text():
    """No name / brand-default name → the exact historical text."""
    assert format_welcome({}) == WELCOME_TEXT
    assert format_welcome({"agent": {"name": ""}}) == WELCOME_TEXT
    assert format_welcome({"agent": {"name": "Windy Fly"}}) == WELCOME_TEXT
    assert format_welcome(None) == WELCOME_TEXT


def test_welcome_named_still_under_telegram_cap():
    """Even a max-length name keeps the message under 4096."""
    assert len(format_welcome({"agent": {"name": "N" * 100}})) <= 4096
