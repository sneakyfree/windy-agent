"""Over budget, the owner can choose to go on today (strand P3.32.6)."""

from __future__ import annotations

import asyncio
import json

import pytest

from windyfly.channels.base import handle_incoming
from windyfly.commands.core import wire_runtime
from windyfly.commands.setup import init_all_commands
from windyfly.memory.cost_tracker import CONTINUE_KEY, check_budget, extra_today
from windyfly.memory.database import Database

CFG = {"costs": {"daily_budget_usd": 1.0}}


@pytest.fixture
def db():
    CFG["costs"] = {"daily_budget_usd": 1.0}          # /budget set changes the live config: reset it
    d = Database(":memory:")
    init_all_commands(db=d, config=CFG)
    wire_runtime(db=d)
    yield d
    d.close()


def _spend(db, usd):
    db.execute("INSERT INTO cost_ledger (id, model, input_tokens, output_tokens, cost_usd) VALUES (?, 'm', 1, 1, ?)",
               (f"c{usd}", usd))
    db.commit()


def _say(text, sender_ctx=None):
    ok, out = asyncio.run(handle_incoming(text, dict(sender_ctx or {"platform": "cli", "channel_id": "b"})))
    assert ok
    return out


def test_over_budget_the_owner_can_continue_today_once(db):
    _spend(db, 1.20)
    blocked = check_budget(db, CFG)
    assert not blocked["allowed"] and "/budget continue" in blocked["alert"]
    assert "keep going today, up to another $1.00" in _say("/budget continue")
    assert check_budget(db, CFG)["allowed"]                                  # 1.20 <= 1.00 + 1.00
    assert "already let me keep going today" in _say("/budget continue")      # once a day
    _spend(db, 0.90)
    again = check_budget(db, CFG)
    assert not again["allowed"] and "the extra you allowed" in again["alert"]


def test_the_extra_is_for_today_only(db):
    db.execute("INSERT INTO soul (id, user_id, key, value) VALUES ('x', 'default', ?, ?)",
               (CONTINUE_KEY, json.dumps({"date": "2000-01-01", "extra": 5.0})))
    db.commit()
    assert extra_today(db) == 0.0
    _spend(db, 1.20)
    assert not check_budget(db, CFG)["allowed"]


def test_the_monthly_budget_is_never_overridden(db):
    cfg = {"costs": {"daily_budget_usd": 1.0, "monthly_budget_usd": 1.0}}
    _spend(db, 1.20)
    from windyfly.memory.cost_tracker import continue_today

    continue_today(db, cfg)
    assert not check_budget(db, cfg)["allowed"]


def test_only_the_owner_can_continue(db, monkeypatch):
    monkeypatch.setenv("WINDY_OWNER_IDS", "discord:111")
    out = _say("/budget continue", {"platform": "discord", "channel_id": "c", "sender_id": "999"})
    assert "owner-only" in out
    assert extra_today(db) == 0.0


def test_budget_shows_and_sets_the_limit_the_gate_uses(db):
    _spend(db, 0.25)
    assert "/ $1.00" in _say("/budget")
    assert "until I restart" in _say("/budget set 2")
    assert "/ $2.00" in _say("/budget")
    assert check_budget(db, CFG)["daily_budget"] == 2.0
