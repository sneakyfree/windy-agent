"""/forget (strand N10.2): the paths around the happy one."""

from __future__ import annotations

import asyncio

import pytest

from windyfly.channels.base import handle_incoming
from windyfly.commands.setup import init_all_commands
from windyfly.memory import forget as fg
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.memory.nodes import upsert_node

CLI = {"platform": "cli", "channel_id": "forget-unit"}


@pytest.fixture
def db():
    from windyfly.commands.core import wire_runtime

    d = Database(":memory:")
    init_all_commands(db=d, config={})
    wire_runtime(db=d)
    save_episode(d, "user", "the blue kayak is in the garage", session_id="s")
    save_episode(d, "user", "the blue car is outside", session_id="s")
    upsert_node(d, "fact", "kayak colour is blue")
    d.commit()
    yield d
    d.close()


def _say(text):
    ok, out = asyncio.run(handle_incoming(text, dict(CLI)))
    assert ok
    return out


def _messages(d):
    return d.fetchone("SELECT COUNT(*) AS n FROM episodes")["n"]


def test_every_word_must_match(db):
    found = fg.find(db, "blue kayak")
    assert {i.kind for i in found} == {"message", "memory"} and len(found) == 2


def test_no_cancels_and_nothing_is_deleted(db):
    _say("/forget blue")
    _say("/forget delete all")
    assert "nothing was deleted" in _say("/forget no")
    assert "nothing waiting" in _say("/forget yes")
    assert _messages(db) == 2


def test_out_of_range_and_bad_numbers_delete_nothing(db):
    _say("/forget blue")
    assert "Pick numbers from 1 to 3" in _say("/forget delete 9")
    assert "Give the numbers" in _say("/forget delete two")
    assert "nothing waiting" in _say("/forget yes")
    assert _messages(db) == 2


def test_one_pick_deletes_only_that_item(db):
    listed = _say("/forget blue car")
    assert "1 thing" in listed
    _say("/forget delete 1")
    assert "1 message" in _say("/forget yes")
    assert _messages(db) == 1 and fg.find(db, "kayak")


def test_a_choice_expires_after_ten_minutes(db, monkeypatch):
    import time as real_time

    _say("/forget blue")
    _say("/forget delete all")
    later = real_time.time() + 601
    monkeypatch.setattr(real_time, "time", lambda: later)
    assert "nothing waiting" in _say("/forget yes")
    assert _messages(db) == 2


def test_nothing_found_says_so(db):
    assert "I don't remember anything" in _say("/forget submarine")
