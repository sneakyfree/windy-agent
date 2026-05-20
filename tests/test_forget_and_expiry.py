"""``/forget`` slash command + correction-skill expiry.

PR #212 made correction skills actually APPLIED. Two follow-ups
needed to make the loop sustainable in production:

  - **Manual demotion via ``/forget <substring>``**: when an auto-
    promoted correction skill is bad advice (false-positive on
    friction, over-cautious correction), the user can demote it
    with a single message rather than needing to find the bridge
    UDS server and a skill UUID.

  - **Automatic expiry**: a correction skill not re-touched in 30
    days has had its underlying fault pattern stop recurring —
    safe to retire from active rotation. Without expiry, every
    historical correction stays in the prompt forever, paying
    ~100 tokens per turn for advice the user no longer needs.
"""

from __future__ import annotations

import pytest

from windyfly.channels.slash_commands import parse_forget_command
from windyfly.memory.database import Database
from windyfly.memory.skills import (
    get_active_correction_skills,
    save_skill,
)
from windyfly.skills.manager import (
    demote_skill,
    demote_skill_by_name,
    expire_stale_correction_skills,
    promote_skill,
)


# ── /forget parser ───────────────────────────────────────────────


class TestForgetParser:

    def test_bare_command(self):
        assert parse_forget_command("/forget") == (True, None)
        assert parse_forget_command("/FORGET") == (True, None)
        assert parse_forget_command("  /forget  ") == (True, None)

    def test_with_argument(self):
        assert parse_forget_command("/forget factual_error") == (True, "factual_error")
        assert parse_forget_command("/forget correction-x") == (True, "correction-x")
        # Preserves user's original case in the substring
        assert parse_forget_command("/forget Factual") == (True, "Factual")

    def test_aliases(self):
        for alias in ("/demote", "/unlearn"):
            assert parse_forget_command(f"{alias} foo")[0] is True

    def test_not_a_command(self):
        assert parse_forget_command("hello") == (False, None)
        assert parse_forget_command("") == (False, None)
        assert parse_forget_command(None) == (False, None)
        # /forgetful is not /forget
        assert parse_forget_command("/forgetful")[0] is False


# ── demote_skill + demote_skill_by_name ──────────────────────────


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def test_demote_skill_clears_promoted(db):
    sid = save_skill(db, "correction-foo", "code", "python")
    promote_skill(db, sid)
    assert demote_skill(db, sid) is True
    row = db.fetchone("SELECT promoted FROM skills WHERE id = ?", (sid,))
    assert row["promoted"] == 0


def test_demote_skill_missing_returns_false(db):
    assert demote_skill(db, "nonexistent-id") is False


def test_demote_skill_by_name_substring(db):
    s1 = save_skill(db, "correction-factual_error", "code", "python")
    s2 = save_skill(db, "correction-factual_error", "code", "python")
    s3 = save_skill(db, "other-skill", "code", "python")
    for s in (s1, s2, s3):
        promote_skill(db, s)
    # Substring match — should demote both factual_error skills
    demoted = demote_skill_by_name(db, "factual_error")
    assert len(demoted) == 2
    # The other-skill stays promoted
    other_row = db.fetchone("SELECT promoted FROM skills WHERE id = ?", (s3,))
    assert other_row["promoted"] == 1


def test_demote_skill_by_name_case_insensitive(db):
    sid = save_skill(db, "correction-PreferenceMiss", "code", "python")
    promote_skill(db, sid)
    demoted = demote_skill_by_name(db, "preferencemiss")
    assert len(demoted) == 1


def test_demote_skill_by_name_no_match(db):
    assert demote_skill_by_name(db, "anything") == []


def test_demote_skill_by_name_empty_arg_no_op(db):
    """An empty substring would LIKE-match everything — defend
    against accidental wipe via empty user input."""
    sid = save_skill(db, "correction-foo", "code", "python")
    promote_skill(db, sid)
    assert demote_skill_by_name(db, "") == []
    assert demote_skill_by_name(db, "   ") == []
    # Skill still promoted
    row = db.fetchone("SELECT promoted FROM skills WHERE id = ?", (sid,))
    assert row["promoted"] == 1


# ── Skill expiry ─────────────────────────────────────────────────


def test_expire_stale_correction_skills_demotes_old(db):
    sid = save_skill(db, "correction-old", "code", "python")
    promote_skill(db, sid)
    # Backdate last_used to 60 days ago
    db.execute(
        "UPDATE skills SET last_used = datetime('now', '-60 days') WHERE id = ?",
        (sid,),
    )
    db.commit()
    n = expire_stale_correction_skills(db, max_age_days=30)
    assert n == 1
    row = db.fetchone("SELECT promoted FROM skills WHERE id = ?", (sid,))
    assert row["promoted"] == 0


def test_expire_skips_recent_skills(db):
    sid = save_skill(db, "correction-recent", "code", "python")
    promote_skill(db, sid)
    # last_used is now (just promoted)
    n = expire_stale_correction_skills(db, max_age_days=30)
    assert n == 0
    row = db.fetchone("SELECT promoted FROM skills WHERE id = ?", (sid,))
    assert row["promoted"] == 1


def test_expire_skips_non_correction_skills(db):
    """Other promoted skills should NOT be touched by the
    correction-only expiry pass."""
    sid = save_skill(db, "my-other-skill", "code", "python")
    promote_skill(db, sid)
    db.execute(
        "UPDATE skills SET last_used = datetime('now', '-60 days') WHERE id = ?",
        (sid,),
    )
    db.commit()
    n = expire_stale_correction_skills(db, max_age_days=30)
    assert n == 0
    row = db.fetchone("SELECT promoted FROM skills WHERE id = ?", (sid,))
    assert row["promoted"] == 1


def test_expire_handles_null_last_used(db):
    """A correction skill with NULL last_used (never used since
    promotion — shouldn't actually happen with the auto-promote
    pattern, but defensive) should fall back to created_at."""
    sid = save_skill(db, "correction-null-last", "code", "python")
    db.execute("UPDATE skills SET promoted = TRUE, last_used = NULL WHERE id = ?", (sid,))
    db.execute(
        "UPDATE skills SET created_at = datetime('now', '-60 days') WHERE id = ?",
        (sid,),
    )
    db.commit()
    n = expire_stale_correction_skills(db, max_age_days=30)
    assert n == 1


def test_get_active_correction_skills_triggers_expiry(db):
    """The read path calls expiry lazily — verify it actually
    happens on `get_active_correction_skills`."""
    sid_old = save_skill(db, "correction-stale", "code", "python")
    promote_skill(db, sid_old)
    db.execute(
        "UPDATE skills SET last_used = datetime('now', '-60 days') WHERE id = ?",
        (sid_old,),
    )
    sid_new = save_skill(db, "correction-fresh", "code", "python")
    promote_skill(db, sid_new)
    db.commit()

    active = get_active_correction_skills(db)
    names = [s["name"] for s in active]
    # Stale should be expired (demoted) and absent; fresh should remain
    assert "correction-stale" not in names
    assert "correction-fresh" in names
    # Confirm the row was actually demoted, not just filtered
    row = db.fetchone("SELECT promoted FROM skills WHERE id = ?", (sid_old,))
    assert row["promoted"] == 0
