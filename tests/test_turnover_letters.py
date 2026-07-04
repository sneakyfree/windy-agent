"""Turnover letters: the /new handoff finally has a writer (Sprint 3).

The prompt assembler has read '## Last Session Handoff' from
turnover_letter nodes since launch, but nothing wrote them — these
tests pin the full loop: session ends → letter written → next
session's prompt carries the handoff.
"""

from __future__ import annotations

import json

import pytest

from windyfly.agent.turnover import (
    MAX_LETTER_CHARS,
    compose_turnover_summary,
    write_turnover_letter,
)
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.memory.nodes import get_nodes_by_type


@pytest.fixture()
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _seed_session(db, session_id="telegram:1:v1", turns=4):
    for i in range(turns):
        save_episode(db, role="user",
                     content=f"question {i}: how do I deploy the site?",
                     session_id=session_id)
        save_episode(db, role="assistant",
                     content=f"answer {i}: run wrangler deploy",
                     session_id=session_id)


class TestCompose:
    def test_empty_session_yields_none(self, db):
        assert compose_turnover_summary(db, "telegram:1:v1") is None

    def test_digest_contains_topics_and_last_reply(self, db):
        _seed_session(db)
        summary = compose_turnover_summary(db, "telegram:1:v1")
        assert summary is not None
        assert "deploy the site" in summary
        assert "wrangler deploy" in summary

    def test_digest_is_bounded(self, db):
        for i in range(10):
            save_episode(db, role="user", content="x" * 500,
                         session_id="telegram:1:v1")
        summary = compose_turnover_summary(db, "telegram:1:v1")
        assert summary is not None
        assert len(summary) <= MAX_LETTER_CHARS


class TestWrite:
    def test_writes_node_the_prompt_reader_consumes(self, db):
        _seed_session(db)
        ok = write_turnover_letter(
            db, None, platform="telegram", channel_id="1",
            session_id="telegram:1:v1",
        )
        assert ok
        letters = get_nodes_by_type(db, "turnover_letter", limit=1)
        assert letters
        meta = json.loads(letters[0]["metadata"])
        assert "deploy the site" in meta["summary"]
        assert meta["session_id"] == "telegram:1:v1"

    def test_updates_in_place_never_accumulates(self, db):
        _seed_session(db, session_id="telegram:1:v1")
        _seed_session(db, session_id="telegram:1:v2")
        write_turnover_letter(db, None, platform="telegram",
                              channel_id="1", session_id="telegram:1:v1")
        write_turnover_letter(db, None, platform="telegram",
                              channel_id="1", session_id="telegram:1:v2")
        letters = get_nodes_by_type(db, "turnover_letter", limit=10)
        assert len(letters) == 1
        assert json.loads(letters[0]["metadata"])["session_id"] == "telegram:1:v2"

    def test_empty_session_writes_nothing(self, db):
        ok = write_turnover_letter(
            db, None, platform="telegram", channel_id="1",
            session_id="telegram:1:v99",
        )
        assert not ok
        assert not get_nodes_by_type(db, "turnover_letter", limit=1)

    def test_never_raises(self, db):
        # A broken DB handle must not break /new.
        class Broken:
            def __getattr__(self, _):
                raise RuntimeError("db is toast")

        assert write_turnover_letter(
            Broken(), None, platform="t", channel_id="1", session_id="s",
        ) is False


class TestEndToEnd:
    def test_next_session_prompt_carries_handoff(self, db):
        from windyfly.agent.prompt import assemble_prompt

        _seed_session(db)
        write_turnover_letter(db, None, platform="telegram",
                              channel_id="1", session_id="telegram:1:v1")
        messages = assemble_prompt(
            config={}, db=db, user_message="hi again",
            session_id="telegram:1:v2",
        )
        joined = "\n".join(
            m["content"] for m in messages if m["role"] == "system"
        )
        assert "Last Session Handoff" in joined
        assert "deploy the site" in joined
