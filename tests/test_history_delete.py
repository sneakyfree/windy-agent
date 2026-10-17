"""/history and the bridge's history.preview/history.delete (strand N10.1)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from windyfly.bridge.uds_server import UDSBridge
from windyfly.channels.base import handle_incoming
from windyfly.commands.setup import init_all_commands
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.memory.write_queue import WriteQueue

CLI = {"platform": "cli", "channel_id": "history-unit"}


@pytest.fixture
def db():
    from windyfly.commands.core import wire_runtime

    d = Database(":memory:")
    init_all_commands(db=d, config={})
    wire_runtime(db=d)
    save_episode(d, "user", "the red kite flew high", session_id="s")
    save_episode(d, "assistant", "kites need wind", session_id="s")
    d.commit()
    yield d
    d.close()


def _say(text):
    ok, out = asyncio.run(handle_incoming(text, dict(CLI)))
    assert ok
    return out


def _n(d):
    return d.fetchone("SELECT COUNT(*) AS n FROM episodes")["n"]


def test_bare_history_still_shows_recent_and_adds_days(db):
    out = _say("/history")
    assert "Recent conversation" in out and "Messages per day" in out


def test_find_then_delete_numbers_needs_a_yes(db):
    assert "1 message" in _say("/history find red kite")
    _say("/history delete 1")
    assert _n(db) == 2
    assert "1 message" in _say("/history yes")
    assert _n(db) == 1


def test_bad_inputs_delete_nothing(db):
    assert "Give a day" in _say("/history delete next tuesday")
    assert "Search first" in _say("/history delete 1")
    assert "nothing waiting" in _say("/history yes")
    _say("/history delete all")
    assert "nothing was deleted" in _say("/history no")
    assert _n(db) == 2


def _bridge(d):
    return UDSBridge({}, d, WriteQueue())


def test_bridge_preview_then_delete_with_a_single_use_token(db):
    b = _bridge(db)
    with patch("windyfly.agent.guest_mode.is_guest_active", return_value=False):
        p = asyncio.run(b._dispatch("history.preview", {"all": True}))
        assert p["counts"] == {"message": 2} and p["token"]
        assert "error" in asyncio.run(b._dispatch("history.delete", {"token": "made-up"}))
        assert _n(db) == 2
        assert asyncio.run(b._dispatch("history.delete", {"token": p["token"]}))["deleted"] == {"message": 2}
        assert "error" in asyncio.run(b._dispatch("history.delete", {"token": p["token"]}))   # used once
    assert _n(db) == 0


def test_bridge_preview_by_query_and_ids_narrows_to_that_message(db):
    b = _bridge(db)
    with patch("windyfly.agent.guest_mode.is_guest_active", return_value=False):
        p = asyncio.run(b._dispatch("history.preview", {"query": "kite"}))
        one = p["items"][0]["id"]
        q = asyncio.run(b._dispatch("history.preview", {"query": "kite", "ids": [one]}))
    assert [i["id"] for i in q["items"]] == [one]


def test_bridge_refuses_in_guest_mode(db):
    b = _bridge(db)
    with patch("windyfly.agent.guest_mode.is_guest_active", return_value=True):
        assert "error" in asyncio.run(b._dispatch("history.preview", {"all": True}))
        assert "error" in asyncio.run(b._dispatch("history.delete", {"token": "x"}))
    assert _n(db) == 2
