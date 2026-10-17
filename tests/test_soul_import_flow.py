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
    for held in (flow._pending, flow._review_lists, flow._confirm, flow._tones):
        held.clear()
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


def _two_flagged(db):
    from windyfly.memory.nodes import upsert_node

    for n in (1, 2):
        upsert_node(db, type="identity", name=f"zqxflag{n}",
                    metadata={"raw_content": f"zqxflag{n}", "import_source": "imported_openclaw",
                              "needs_review": True}, confidence=0.3, epistemic_status="speculative")
    db.commit()
    flow._review_lists.clear()


def test_keep_and_drop_need_a_fresh_list_and_valid_numbers(db, monkeypatch):
    import time as real_time

    _two_flagged(db)
    assert "List them first" in _say("/soul keep 1")             # numbers mean nothing before a list
    assert "Usage" in _say("/soul drop")
    _say("/soul review")
    assert "from 1 to 2" in _say("/soul keep 3")
    assert "from 1 to 2" in _say("/soul drop 0")
    assert "Give the numbers" in _say("/soul keep one")
    later = real_time.time() + flow.REVIEW_LIST_FOR_S + 1
    monkeypatch.setattr(real_time, "time", lambda: later)
    assert "List them first" in _say("/soul drop 1")             # an old list is not trusted
    assert db.fetchone("SELECT COUNT(*) AS n FROM nodes")["n"] == 2


def test_review_replies_are_not_saved_as_episodes(db):
    _two_flagged(db)
    _say("/soul review")
    _say("/soul review 1")
    _say("/soul keep 1")
    _say("/soul review")
    _say("/soul drop all")
    _say("/soul yes")
    assert db.fetchone("SELECT COUNT(*) AS n FROM episodes")["n"] == 0
    assert "zqxflag2" not in _everywhere(db)                     # dropped; the kept one stays


def test_drop_waits_for_a_yes_and_review_starts_over(db):
    _two_flagged(db)
    assert "Nothing is waiting for a yes" in _say("/soul yes")
    _say("/soul review")
    asked = _say("/soul drop 2")
    assert "Delete 1 item for good?" in asked and "2. identity from openclaw" in asked
    assert db.fetchone("SELECT COUNT(*) AS n FROM nodes")["n"] == 2   # asking deletes nothing
    _say("/soul review")                                           # starting over drops the question
    assert "Nothing is waiting for a yes" in _say("/soul yes")
    _say("/soul drop 2")
    assert "Deleted 1 item" in _say("/soul yes")
    assert [r["name"] for r in db.fetchall("SELECT name FROM nodes")] == ["zqxflag1"]


def test_a_kept_item_is_not_deleted_by_an_older_drop_question(db):
    _two_flagged(db)
    _say("/soul review")
    _say("/soul drop all")
    _say("/soul review")
    _say("/soul keep 1")
    flow._confirm["cli:soul-flow"] = (__import__("time").time(), "drop",
                                      [r["id"] for r in db.fetchall("SELECT id FROM nodes")])
    assert "Deleted 1 item" in _say("/soul yes")                  # only the one still flagged
    assert [r["name"] for r in db.fetchall("SELECT name FROM nodes")] == ["zqxflag1"]


def test_guest_mode_turns_soul_import_and_review_off(db, monkeypatch, tmp_path):
    flag = tmp_path / ".guest"
    flag.write_text("{}")
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(flag))
    _two_flagged(db)
    for verb in ("/soul import", "/soul approve", "/soul review", "/soul keep 1", "/soul drop all", "/soul yes",
                 "/soul apply", "/soul undo"):
        assert "owner-only" in _say(verb), verb                  # the channel already treats a guest as a user
    # and the flow refuses on its own, for any path that reaches it (the bridge, the file handlers)
    for reply in (flow.start(KEY), flow.approve(db, KEY), flow.review(db, KEY, []), flow.keep(db, KEY, ["1"]),
                  flow.drop(db, KEY, ["all"]), flow.confirm(db, KEY), flow.apply(db, KEY),
                  flow.undo(db, KEY)):
        assert reply == flow.GUEST_OFF
    assert not flow.awaiting_file(KEY)                           # both file handlers ask this first
    assert "guest mode" in flow.take_file(db, KEY, io.BytesIO(_export()), "conversations.json", max_bytes=10**6)
    assert not list(tmp_path.glob("windy-soul-intake-*"))
    assert db.fetchone("SELECT COUNT(*) AS n FROM nodes")["n"] == 2
    flag.unlink()
    assert "Send me your export" in _say("/soul import")


def test_guest_mode_ignores_a_file_sent_on_matrix(db, monkeypatch, tmp_path):
    from tests.test_matrix_bot import _make_config
    from windyfly.agent.capabilities.descriptor import Band
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot
    from windyfly.memory.write_queue import WriteQueue

    bot = WindyFlyMatrixBot(_make_config(), db, WriteQueue())
    bot.client.room_send = AsyncMock()
    bot.client.download = AsyncMock(return_value=MagicMock(body=_export()))
    room, ev = MagicMock(), MagicMock()
    room.room_id = "!dm:example.test"
    ev.sender, ev.body, ev.url = "@owner:example.test", "conversations.json", "mxc://example.test/x"
    ev.source = {"content": {"info": {"size": 2000}}}
    monkeypatch.setattr("windyfly.channels.identity.resolve_band", lambda p, s, config=None: Band.OWNER)
    flow.start("matrix:!dm:example.test")                       # an import was waiting, then guest mode came on
    flag = tmp_path / ".guest"
    flag.write_text("{}")
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(flag))
    asyncio.run(bot._on_file(room, ev))
    bot.client.download.assert_not_awaited()
    assert not db.fetchone("SELECT COUNT(*) AS n FROM nodes")["n"]


def test_openclaw_tone_is_read_without_pyyaml(tmp_path):
    from windyfly.soul_import.openclaw import _config_tone

    (tmp_path / "config.yaml").write_text("humor: 8  # funny\nformality: 3\nnested:\n  formality: 1\nverbosity: lots\n")
    assert _config_tone(tmp_path) == {"humor": 8.0, "formality": 3.0}      # top level numbers only
    (tmp_path / "config.yaml").unlink()
    (tmp_path / "config.json").write_text('{"humor": 2, "formality": true, "verbosity": 7.5, "x": 1}')
    assert _config_tone(tmp_path) == {"humor": 2, "verbosity": 7.5}
    (tmp_path / "config.json").write_text("{not json")
    assert _config_tone(tmp_path) == {}


def test_only_whole_slider_numbers_from_0_to_10_are_offered():
    from windyfly.soul_import.style import tone_values

    assert tone_values({"humor": 7.6, "formality": 11, "verbosity": True, "traits": ["x"]}) == {"humor": 8}
    assert tone_values({"humor": "9", "formality": -1}) == {}
    assert tone_values(None) == {}


def test_apply_needs_an_approved_import_and_the_offer_expires(db, monkeypatch):
    import time as real_time

    assert "no imported tone" in _say("/soul apply")
    flow.offer_tone(KEY, {"humor": 9}, "openclaw")
    assert "humor: 5 -> 9" in _say("/soul apply")
    later = real_time.time() + flow.PENDING_FOR_S + 1
    monkeypatch.setattr(real_time, "time", lambda: later)
    assert "no imported tone" in _say("/soul apply")
    assert "Nothing is waiting for a yes" in _say("/soul yes")          # the question expired too


def test_one_question_at_a_time_a_later_apply_replaces_a_pending_drop(db):
    """Hub on PR 3: drop asked, then apply asked, then yes: the yes applies the tone and deletes nothing."""
    from windyfly.control_panel import get_sliders

    _two_flagged(db)
    _say("/soul review")
    assert "Delete 2 items" in _say("/soul drop all")
    flow.offer_tone(KEY, {"humor": 9}, "openclaw")
    _say("/soul apply")
    done = _say("/soul yes")
    assert done.startswith("Applied the imported tone: humor 9") and "Deleted" not in done
    assert db.fetchone("SELECT COUNT(*) AS n FROM nodes")["n"] == 2
    assert int(float(get_sliders(db)["humor"])) == 9
    assert "Nothing is waiting for a yes" in _say("/soul yes")       # used once

    _say("/soul undo")
    _say("/soul apply")                                              # and the other way round
    _say("/soul review")
    _say("/soul drop 1")
    assert _say("/soul yes").startswith("Deleted 1 item")
    assert int(float(get_sliders(db)["humor"])) == 5                 # the apply question was dropped

