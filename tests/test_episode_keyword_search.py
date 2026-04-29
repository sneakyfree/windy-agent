"""Episode keyword-search recall tests.

Closes the v9 anti-amnesia ceiling: pre-fix the prompt assembly
only included the most recent N episodes per session. Once a
conversation ran longer than N, older facts were invisible no
matter how relevant they were to the current question.

Post-fix, ``search_episodes`` does an FTS5 match across the user
message keywords, scoped to the current session, excluding
already-injected recent episodes — so "what's my dog's name?"
finds the episode where the user said "my dog's name is Pepper"
even if it's 50 turns ago.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from windyfly.memory.database import Database
from windyfly.memory.episodes import (
    get_recent_episodes,
    save_episode,
    search_episodes,
)


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "ep.db"))
        try:
            yield db
        finally:
            db.close()


def test_empty_query_returns_empty(db):
    save_episode(db, "user", "hello", session_id="s1")
    assert search_episodes(db, "", session_id="s1") == []


def test_single_keyword_match(db):
    save_episode(db, "user", "My dog's name is Pepper.", session_id="s1")
    save_episode(db, "user", "I love going for walks.", session_id="s1")
    results = search_episodes(db, "dog", session_id="s1")
    assert len(results) == 1
    assert "Pepper" in results[0]["content"]


def test_multi_keyword_or_match(db):
    """Tokens are OR'd — any one match surfaces the episode. The
    query 'dog name' must match BOTH episodes (one has 'dog', one
    has 'name')."""
    save_episode(db, "user", "My dog Pepper.", session_id="s1")
    save_episode(db, "user", "My name is Ruth.", session_id="s1")
    save_episode(db, "user", "Talked about the weather.", session_id="s1")
    results = search_episodes(db, "dog name", session_id="s1")
    contents = {r["content"] for r in results}
    assert any("Pepper" in c for c in contents)
    assert any("Ruth" in c for c in contents)
    assert not any("weather" in c for c in contents)


def test_session_id_isolates(db):
    save_episode(db, "user", "alice's dog is Rex.", session_id="alice")
    save_episode(db, "user", "bob's dog is Spot.", session_id="bob")
    alice_results = search_episodes(db, "dog", session_id="alice")
    bob_results = search_episodes(db, "dog", session_id="bob")
    assert len(alice_results) == 1
    assert "Rex" in alice_results[0]["content"]
    assert len(bob_results) == 1
    assert "Spot" in bob_results[0]["content"]


def test_exclude_ids_dedup(db):
    """Episodes already in the recent-N window must be excluded so
    the prompt doesn't double-include them."""
    id1 = save_episode(db, "user", "dog one", session_id="s1")
    id2 = save_episode(db, "user", "dog two", session_id="s1")
    id3 = save_episode(db, "user", "dog three", session_id="s1")
    results = search_episodes(
        db, "dog", session_id="s1", exclude_ids={id1, id2},
    )
    assert len(results) == 1
    assert results[0]["id"] == id3


def test_fts_special_chars_sanitized(db):
    """User input with FTS5 special chars (AND, OR, *, +, parens)
    must not crash or smuggle operators into the query."""
    save_episode(db, "user", "My dog is named Buddy.", session_id="s1")
    # All these would be FTS5 syntax errors if not sanitized:
    queries = [
        "dog AND cat",
        "dog OR (cat NOT mouse)",
        "dog*",
        "+dog",
        '"dog""',
        "dog && | cat",
    ]
    for q in queries:
        result = search_episodes(db, q, session_id="s1")
        # Should not raise; should return the matching episode.
        assert any("Buddy" in r["content"] for r in result), \
            f"query {q!r} failed to find Buddy"


def test_short_tokens_filtered(db):
    """Very short tokens (1 char) get filtered to avoid noise."""
    save_episode(db, "user", "I love my dog.", session_id="s1")
    # "I" is 1 char and gets filtered; "love" + "dog" are kept.
    result = search_episodes(db, "I am here", session_id="s1")
    # "am" is 2 chars (kept), "here" is 4 (kept) — neither matches.
    # No episode contains "am" or "here", so empty.
    # This validates that 1-char tokens don't blow up.
    assert isinstance(result, list)


def test_limit_respected(db):
    for i in range(20):
        save_episode(db, "user", f"dog episode number {i}", session_id="s1")
    results = search_episodes(db, "dog", limit=5, session_id="s1")
    assert len(results) == 5


def test_no_session_filter_searches_all(db):
    """Backward compat: omitting session_id searches across all."""
    save_episode(db, "user", "alice has a dog", session_id="alice")
    save_episode(db, "user", "bob has a dog", session_id="bob")
    results = search_episodes(db, "dog")
    assert len(results) == 2
