"""The owner-facing soul import (strand P2.21.13): /soul import -> file -> preview -> approve or reject."""

from __future__ import annotations

import asyncio
import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from windyfly.channels.base import handle_incoming
from windyfly.commands.setup import init_all_commands
from windyfly.memory.database import Database
from windyfly.soul_import import flow
from windyfly.soul_import import intake as ik

CANARY = "zqxcanary4242"
CLI = {"platform": "cli", "channel_id": "soul-flow"}
KEY = "cli:soul-flow"


def _export() -> bytes:
    return json.dumps([{
        "title": f"Planning the {CANARY} trip",
        "mapping": {"a": {"message": {"author": {"role": "user"},
                                      "content": {"parts": [f"I prefer window seats on the {CANARY} trip"]}}}},
    }]).encode()


@pytest.fixture
def db(monkeypatch, tmp_path):
    from windyfly.commands.core import wire_runtime

    monkeypatch.setattr(ik, "_base_dir", lambda: tmp_path)
    d = Database(":memory:")
    init_all_commands(db=d, config={})
    wire_runtime(db=d)
    flow._pending.clear()
    yield d
    flow._pending.clear()
    d.close()


def _say(text):
    ok, out = asyncio.run(handle_incoming(text, dict(CLI)))
    assert ok
    return out


def _everywhere(d) -> str:
    rows = []
    for table in ("nodes", "episodes", "events", "agent_actions", "skills"):
        rows += [json.dumps(dict(r), default=str) for r in d.fetchall(f"SELECT * FROM {table}")]
    return "\n".join(rows)


def test_approve_keeps_the_import_and_wipes_the_file(db, tmp_path):
    assert "Send me your export" in _say("/soul import")
    preview = flow.take_file(db, KEY, io.BytesIO(_export()), "conversations.json", max_bytes=10**6)
    assert "Soul Preview" in preview and "/soul approve" in preview
    assert not _everywhere(db).count(CANARY)                     # the preview wrote nothing
    assert "Imported" in _say("/soul approve")
    assert CANARY in _everywhere(db)                             # now it is kept, as imported data
    assert not list(tmp_path.glob("windy-soul-intake-*"))        # and the upload is gone


def test_reject_leaves_the_export_nowhere(db, tmp_path):
    _say("/soul import")
    flow.take_file(db, KEY, io.BytesIO(_export()), "conversations.json", max_bytes=10**6)
    assert "Nothing was kept" in _say("/soul reject")
    assert CANARY not in _everywhere(db)                         # not in memory, history, events or actions
    assert not list(tmp_path.glob("windy-soul-intake-*"))


def test_a_file_nobody_asked_for_is_not_taken(db, tmp_path):
    assert "wasn't expecting" in flow.take_file(db, KEY, io.BytesIO(_export()), "conversations.json",
                                                 max_bytes=10**6)
    assert not list(tmp_path.glob("windy-soul-intake-*"))
    assert "no import waiting" in _say("/soul approve")


def test_an_import_left_waiting_expires(db, monkeypatch):
    import time as real_time

    _say("/soul import")
    later = real_time.time() + flow.PENDING_FOR_S + 1
    monkeypatch.setattr(real_time, "time", lambda: later)
    assert not flow.awaiting_file(KEY)


def test_matrix_takes_only_the_owners_awaited_file_and_checks_the_size_first(db, monkeypatch):
    from tests.test_matrix_bot import _make_config
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot
    from windyfly.memory.write_queue import WriteQueue

    bot = WindyFlyMatrixBot(_make_config(), db, WriteQueue())
    bot.client.room_send = AsyncMock()
    bot.client.download = AsyncMock(return_value=MagicMock(body=_export()))
    room = MagicMock()
    room.room_id = "!dm:example.test"
    ev = MagicMock()
    ev.sender, ev.body, ev.url = "@owner:example.test", "conversations.json", "mxc://example.test/x"
    ev.source = {"content": {"info": {"size": 30 * 1024 * 1024}}}
    owner = {"v": True}
    from windyfly.agent.capabilities.descriptor import Band
    monkeypatch.setattr("windyfly.channels.identity.resolve_band",
                        lambda p, s, config=None: Band.OWNER if owner["v"] else Band.USER)

    asyncio.run(bot._on_file(room, ev))                          # nobody asked: ignored
    bot.client.room_send.assert_not_awaited()
    flow.start("matrix:!dm:example.test")
    owner["v"] = False
    asyncio.run(bot._on_file(room, ev))                          # not the owner: ignored
    bot.client.room_send.assert_not_awaited()
    owner["v"] = True
    asyncio.run(bot._on_file(room, ev))                          # 30 MB declared: refused, not downloaded
    bot.client.download.assert_not_awaited()
    assert "more than Windy Chat" in bot.client.room_send.await_args.args[2]["body"]
    ev.source = {"content": {"info": {"size": 2000}}}
    asyncio.run(bot._on_file(room, ev))                          # owner, awaited, small: previewed
    assert "Soul Preview" in bot.client.room_send.await_args.args[2]["body"]
    # Hub's A1: the preview (and every /soul reply) never becomes a conversation episode,
    # so no export text reaches a later model prompt.
    assert db.fetchone("SELECT COUNT(*) AS n FROM episodes")["n"] == 0



def test_soul_command_replies_are_not_saved_as_episodes(db):
    """Hub's A1, through the channel command path: /soul import, /soul approve and /soul reject leave no episode."""
    _say("/soul import")
    flow.take_file(db, KEY, io.BytesIO(_export()), "conversations.json", max_bytes=10**6)
    _say("/soul approve")
    _say("/soul import")
    _say("/soul reject")
    assert db.fetchone("SELECT COUNT(*) AS n FROM episodes")["n"] == 0
