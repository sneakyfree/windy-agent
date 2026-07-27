"""Read-on-waking — closing the /new memory gap.

``/new`` rolls the session_id, and both the recent-episode window and
the anti-amnesia search are scoped to session_id. So the turn after a
reset the agent had ZERO conversation history and nothing telling it to
go look, even though ``memory.read_range`` exists and its own docstring
calls itself "THE tool for catching up after a reset." Measured against
the assembled prompt, fact recall went 100% -> 0% across the boundary
with the turnover letter present — the letter digests only the last
handful of user lines, so it carried none of the facts.

The dead ``memory_depth`` slider (advertised and billed at $0.80/point
with no consumer anywhere in src/) now drives how far back the agent
reads when it wakes.

These tests pin the behaviour that makes it safe as well as the
behaviour that makes it work: it must fire ONLY on waking, must stay
bounded, must never hand a stranger the owner's history, and must never
read a different conversation's episodes.
"""

from __future__ import annotations

from windyfly.agent.capabilities import Band
from windyfly.agent.prompt import assemble_prompt
from windyfly.control_panel import SLIDER_INFO
from windyfly.memory.database import Database
from windyfly.memory.episodes import (
    get_prior_session_episodes,
    save_episode,
)
from windyfly.memory.soul import upsert_soul

WAKE_MARKER = "Catching up"
SECRET = "Biscuit the beagle"


def _config() -> dict:
    return {
        "agent": {"default_model": "gpt-4o-mini"},
        "memory": {"db_path": ":memory:", "max_nodes_per_context": 10},
        "personality": {"soul_path": "SOUL.md", "autonomy": 5},
    }


def _seed(db: Database, session_id: str, turns: int = 30) -> None:
    for i in range(turns):
        save_episode(db, "user", f"turn {i}: my dog is {SECRET}",
                     session_id=session_id)
        save_episode(db, "assistant", f"noted turn {i}",
                     session_id=session_id)


def _set_depth(db: Database, value: int) -> None:
    upsert_soul(db, "slider_memory_depth", str(value))


def _text(messages) -> str:
    return "\n\n".join(m["content"] for m in messages)


def _woken_db(depth: int = 5) -> Database:
    db = Database(":memory:")
    _seed(db, "telegram:555:v0")
    _set_depth(db, depth)
    return db


# ── The gap itself ─────────────────────────────────────────────────


def test_waking_turn_recovers_facts_from_before_the_reset() -> None:
    """The regression this feature exists for: after /new, a fact
    established in the previous session is physically in the prompt."""
    db = _woken_db()
    msgs = assemble_prompt(_config(), db, "what is my dog's name?",
                           "telegram:555:v1")
    blob = _text(msgs)
    assert WAKE_MARKER in blob
    assert SECRET in blob


def test_without_the_block_the_reset_loses_everything() -> None:
    """Guards the measurement: with the slider at 0 the old behaviour
    is exactly reproduced — proving the recovery above comes from this
    feature and not from some other block picking up the slack."""
    db = _woken_db(depth=0)
    blob = _text(assemble_prompt(_config(), db, "what is my dog's name?",
                                 "telegram:555:v1"))
    assert WAKE_MARKER not in blob
    assert SECRET not in blob


# ── On waking, NOT every turn ──────────────────────────────────────


def test_block_is_absent_once_the_session_has_history() -> None:
    db = _woken_db()
    save_episode(db, "user", "hi", session_id="telegram:555:v1")
    save_episode(db, "assistant", "hello", session_id="telegram:555:v1")
    blob = _text(assemble_prompt(_config(), db, "and now?",
                                 "telegram:555:v1"))
    assert WAKE_MARKER not in blob


def test_first_contact_has_nothing_to_catch_up_on() -> None:
    """A brand-new agent must not claim to be resuming anything."""
    db = Database(":memory:")
    _set_depth(db, 10)
    blob = _text(assemble_prompt(_config(), db, "hello?",
                                 "telegram:555:v0"))
    assert WAKE_MARKER not in blob


# ── Bounded by construction (#8: stability outranks capability) ────


def test_depth_scales_and_stays_bounded() -> None:
    db = Database(":memory:")
    # Long turns so the CHARACTER budget binds rather than the history
    # simply running out — a smaller fixture makes every depth look
    # identical and the assertion below vacuous.
    filler = " and then we talked about the weather at length" * 4
    for i in range(200):
        save_episode(db, "user", f"turn {i}: my dog is {SECRET}.{filler}",
                     session_id="telegram:555:v0")

    sizes = {}
    for depth in (1, 3, 5, 10):
        _set_depth(db, depth)
        sizes[depth] = len(_text(assemble_prompt(
            _config(), db, "hi", "telegram:555:v1")))

    assert sizes[1] < sizes[3] < sizes[5] < sizes[10], sizes
    # The honey-badger claim is that the prompt is bounded by
    # construction. Even the deepest setting must stay far inside any
    # model's window.
    assert sizes[10] < 40_000, sizes


def test_budget_truncates_rather_than_dumping_everything() -> None:
    db = Database(":memory:")
    _seed(db, "telegram:555:v0", turns=400)
    rows = get_prior_session_episodes(
        db, "telegram:555:v1", char_budget=1_000,
    )
    assert rows, "should return something"
    assert sum(len(r["content"]) for r in rows) <= 1_000


def test_zero_budget_reads_nothing() -> None:
    db = Database(":memory:")
    _seed(db, "telegram:555:v0")
    assert get_prior_session_episodes(
        db, "telegram:555:v1", char_budget=0,
    ) == []


# ── Privacy: never hand a stranger the owner's history ─────────────


def test_non_owner_bands_get_no_waking_context() -> None:
    db = _woken_db(depth=10)
    for band in (Band.SANDBOX, Band.USER):
        blob = _text(assemble_prompt(
            _config(), db, "hi", "telegram:555:v1", band=band,
        ))
        assert WAKE_MARKER not in blob, band
        assert SECRET not in blob, band


def test_owner_bands_do_get_waking_context() -> None:
    db = _woken_db(depth=10)
    for band in (Band.TRUSTED, Band.OWNER):
        blob = _text(assemble_prompt(
            _config(), db, "hi", "telegram:555:v1", band=band,
        ))
        assert WAKE_MARKER in blob, band


# ── Scoping: the right conversation, and only that one ─────────────


def test_does_not_read_a_different_channel() -> None:
    db = Database(":memory:")
    _seed(db, "telegram:555:v0")
    save_episode(db, "user", "my bird is Mrs Kowalczyk the parrot",
                 session_id="telegram:999:v0")
    _set_depth(db, 10)
    blob = _text(assemble_prompt(_config(), db, "hi", "telegram:555:v1"))
    assert "Kowalczyk" not in blob
    assert SECRET in blob


def test_does_not_reread_the_current_session() -> None:
    db = Database(":memory:")
    _seed(db, "telegram:555:v0")
    rows = get_prior_session_episodes(
        db, "telegram:555:v0", char_budget=100_000,
    )
    assert all(r["session_id"] != "telegram:555:v0" for r in rows)


def test_unparseable_session_id_reads_nothing() -> None:
    """A wrong-conversation read is far worse than no read, so a
    session id we can't parse must NOT fall back to an unscoped query.
    """
    db = Database(":memory:")
    _seed(db, "telegram:555:v0")
    assert get_prior_session_episodes(
        db, "legacy-no-colons", char_budget=100_000,
    ) == []


def test_channel_id_wildcards_cannot_widen_the_match() -> None:
    """A channel id is normally numeric, but an unescaped LIKE
    wildcard would splice another conversation's history into this
    prompt."""
    db = Database(":memory:")
    save_episode(db, "user", "victim channel secret",
                 session_id="telegram:555:v0")
    rows = get_prior_session_episodes(
        db, "telegram:%:v1", char_budget=100_000,
    )
    assert rows == []


# ── The slider is no longer a lie ──────────────────────────────────


def test_memory_depth_advertises_what_it_now_does() -> None:
    """It was billed at $0.80/point while doing nothing. Whatever the
    panel says about it must at least describe waking behaviour."""
    desc = SLIDER_INFO["memory_depth"]["description"].lower()
    assert "wake" in desc or "waking" in desc or "/new" in desc
