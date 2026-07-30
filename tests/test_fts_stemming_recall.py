"""FTS5 stemming recall — the vocabulary-drift hole.

Grandma does not reuse her own words. She says "im allergic to
penicillin" on Tuesday and asks "what did the doctor say about my
allergies" on Friday. Under the default ``unicode61`` tokenizer those
are two unrelated strings, the episode never comes back from FTS5, and
the fact never reaches the model — a Principle #7 failure that looks
exactly like the agent forgetting.

Migration 11 rebuilds ``episodes_fts`` with the porter stemmer, which
folds both to ``allergi``.

The migration test matters more than the fresh-DB test: every existing
user already has a ``unicode61`` index on disk, so that is where the
bug actually lives.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from windyfly.agent.prompt import _extract_keywords
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode, search_episodes


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as td:
        yield str(Path(td) / "stem.db")


# --- the recall property itself ---------------------------------------


@pytest.mark.parametrize(
    "stored,queried",
    [
        ("my grandson plays the trombone in the school band", "playing"),
        ("i walk the dog every morning down by the creek", "dogs"),
        ("the pharmacy called about my prescription", "prescriptions"),
        ("i pick up my pills at the pharmacy on Union Street", "pharmacies"),
        ("Harold and i got married in 1968", "marry"),
    ],
)
def test_word_variants_match(db_path, stored, queried):
    """A morphological variant of a stored word must retrieve it.

    Scope note, verified against SQLite rather than assumed: porter
    folds INFLECTIONAL endings (-s, -ing, -ed, -ies) and nothing more.
    It does NOT relate ``allergic`` to ``allergies``, nor ``prescribed``
    to ``prescription`` — those are derivational, and no stemmer in
    SQLite reaches them. Only pairs porter genuinely folds belong here;
    an aspirational case would be a test that encodes a wish.
    """
    db = Database(db_path)
    try:
        save_episode(db, "user", stored, session_id="s1")
        results = search_episodes(db, queried, session_id="s1")
        assert results, f"{queried!r} failed to retrieve {stored!r}"
    finally:
        db.close()


def test_stemming_does_not_match_unrelated_text(db_path):
    """Stemming must not turn search into a firehose.

    ``memory.search``'s contract is that no match is honest. Porter is
    an aggressive stemmer, so guard the other direction too.
    """
    db = Database(db_path)
    try:
        save_episode(db, "user", "i bank at Adirondack Trust", session_id="s1")
        assert search_episodes(db, "helicopter", session_id="s1") == []
    finally:
        db.close()


# --- the upgrade path, where the bug actually lives -------------------


def _downgrade_fts_to_unicode61(path: str) -> None:
    """Recreate the pre-migration-11 on-disk state."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        DROP TABLE IF EXISTS episodes_fts;
        CREATE VIRTUAL TABLE episodes_fts USING fts5(
            content, summary, content='episodes', content_rowid='rowid');
        INSERT INTO episodes_fts(episodes_fts) VALUES('rebuild');
        DELETE FROM schema_version WHERE version = 11;
        """
    )
    conn.commit()
    conn.close()


def test_migration_upgrades_an_existing_unicode61_database(db_path):
    """An existing DB gets stemming without losing episodes."""
    db = Database(db_path)
    try:
        save_episode(db, "user", "i walk the dog every morning", session_id="s1")
        save_episode(db, "user", "my dog is a beagle named Biscuit", session_id="s1")
    finally:
        db.close()

    _downgrade_fts_to_unicode61(db_path)

    # Confirm the downgrade genuinely reproduces the bug, so a passing
    # test below cannot be an artifact of the fixture doing nothing.
    conn = sqlite3.connect(db_path)
    stale = conn.execute(
        "SELECT count(*) FROM episodes_fts WHERE episodes_fts MATCH ?",
        ('"walking"',),
    ).fetchone()[0]
    conn.close()
    assert stale == 0, "fixture did not reproduce the unicode61 bug"

    # Reopening runs migration 11.
    db = Database(db_path)
    try:
        assert search_episodes(db, "walking", session_id="s1")
        # Nothing was lost in the rebuild.
        assert search_episodes(db, "beagle", session_id="s1")
        rows = db.fetchall("SELECT count(*) AS c FROM episodes")
        assert rows[0]["c"] == 2
    finally:
        db.close()


def test_migration_is_idempotent(db_path):
    """Opening the DB repeatedly must not re-run or corrupt anything."""
    for _ in range(3):
        db = Database(db_path)
        try:
            save_episode(db, "user", "the doctor called about my pills", session_id="s1")
        finally:
            db.close()

    db = Database(db_path)
    try:
        assert search_episodes(db, "pill", session_id="s1")
        versions = db.fetchall("SELECT version FROM schema_version WHERE version = 11")
        assert len(versions) == 1
    finally:
        db.close()


def test_migration_stays_inside_the_callers_transaction(db_path):
    """The migration must not commit the transaction it runs in.

    ``_run_migrations`` opens ``BEGIN EXCLUSIVE`` and rolls back on any
    exception, which is the only thing standing between a failed upgrade
    and a database with no ``episodes_fts`` at all. That guarantee is
    void if the migration body calls ``executescript``, because it
    issues an implicit COMMIT before running — the DROP would then be
    durable and a later failure could not be undone.

    The failure mode is silent, which is what makes it serious:
    ``search_episodes`` swallows its own exceptions and returns [], so
    an agent with no FTS table doesn't crash. It just stops remembering.

    This asserts the property directly — run the migration inside a
    transaction, roll back, and require the database to be untouched.
    """
    from windyfly.memory import database as db_mod

    db = Database(db_path)
    try:
        save_episode(db, "user", "my dog is a beagle named Biscuit", session_id="s1")
    finally:
        db.close()

    _downgrade_fts_to_unicode61(db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN EXCLUSIVE")
        db_mod._migration_11_fts_porter_stemming(conn)
        conn.rollback()

        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'episodes_fts'"
        ).fetchone()
        assert ddl is not None, "rollback left the database with no FTS table"
        assert "porter" not in ddl[0].lower(), (
            "the migration committed the caller's transaction — rollback "
            "could not undo it, so a mid-upgrade failure would be permanent"
        )
        version = conn.execute(
            "SELECT count(*) FROM schema_version WHERE version = 11"
        ).fetchone()[0]
        assert version == 0, "schema_version 11 survived a rollback"

        # And the pre-migration index still answers.
        hits = conn.execute(
            "SELECT count(*) FROM episodes_fts WHERE episodes_fts MATCH ?",
            ('"beagle"',),
        ).fetchone()[0]
        assert hits == 1
    finally:
        conn.close()


# --- the keyword cap --------------------------------------------------


def test_extract_keywords_keeps_the_sixth_token():
    """English buries the answer-bearing noun at the end.

    Five content words of filler followed by the one word that
    identifies the memory — the sixth slot is the one that matters.
    """
    kws = _extract_keywords("whats the date i always get sad about in june").split()
    assert "june" in kws


def test_extract_keywords_still_caps():
    """Six, not unbounded — a wider OR dilutes bm25 for no gain."""
    kws = _extract_keywords(
        "biscuit trombone penicillin walgreens adirondack buick cadillac boston"
    ).split()
    assert len(kws) == 6
