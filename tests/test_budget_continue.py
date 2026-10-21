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


# ── Hub on #601 ─────────────────────────────────────────────────────────────────────────────

def _ask(text, platform, sender):
    return asyncio.run(handle_incoming(text, {"platform": platform, "channel_id": "room", "sender_id": sender}))


def test_a_capped_month_refuses_continue_and_keeps_the_grant(db):
    CFG["costs"] = {"daily_budget_usd": 1.0, "monthly_budget_usd": 1.5}
    _spend(db, 1.60)
    blocked = check_budget(db, CFG)
    assert not blocked["allowed"] and "monthly budget" in blocked["alert"]
    assert "/budget continue" not in blocked["alert"]                     # it couldn't help
    out = _say("/budget continue")
    assert "monthly budget" in out and "keep going today, up to" not in out
    assert extra_today(db) == 0.0                                         # the grant is kept
    CFG["costs"]["monthly_budget_usd"] = 10.0                              # the owner raises the month
    assert "keep going today, up to another $1.00" in _say("/budget continue")


def test_the_daily_block_offers_continue_only_while_the_month_has_room(db):
    CFG["costs"] = {"daily_budget_usd": 1.0, "monthly_budget_usd": 50.0}
    _spend(db, 1.20)
    assert "/budget continue" in check_budget(db, CFG)["alert"]


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf", "infinity", "abc"])
def test_budget_set_refuses_a_value_that_is_not_a_number(db, value):
    out = _say(f"/budget set {value}")
    assert "number of dollars" in out
    assert CFG["costs"]["daily_budget_usd"] == 1.0
    assert check_budget(db, CFG)["allowed"]


@pytest.mark.parametrize("bad", [0, -3, "0"])
def test_a_daily_budget_of_zero_or_less_uses_the_default_and_warns_once(db, caplog, monkeypatch, bad):
    from windyfly.memory import cost_tracker as ct

    monkeypatch.setattr(ct, "_warned_daily_not_positive", False)
    CFG["costs"] = {"daily_budget_usd": bad}
    with caplog.at_level("WARNING", logger="windyfly.memory.cost_tracker"):
        assert check_budget(db, CFG)["allowed"]
        assert check_budget(db, CFG)["daily_budget"] == ct.DEFAULT_DAILY_BUDGET_USD
    assert sum("0 or less" in r.getMessage() for r in caplog.records) == 1
    assert "nothing to extend" not in _say("/budget continue")
    assert extra_today(db) == ct.DEFAULT_DAILY_BUDGET_USD


def test_eight_threads_get_one_grant_and_one_row(tmp_path):
    import threading

    from windyfly.memory.cost_tracker import continue_today

    d = Database(str(tmp_path / "w.db"))
    try:
        results, start = [], threading.Barrier(8)

        def go():
            start.wait(5)
            results.append(continue_today(d, {"costs": {"daily_budget_usd": 1.0}}))

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert sum(1 for r in results if r.get("granted")) == 1 and len(results) == 8
        rows = d.fetchall("SELECT id FROM soul WHERE key = ?", (CONTINUE_KEY,))
        assert len(rows) == 1
    finally:
        d.close()


@pytest.mark.parametrize("platform,owner", [("matrix", "@owner:example.test"), ("telegram", "4242")])
def test_the_owner_continues_from_matrix_and_telegram_and_others_cannot(db, monkeypatch, platform, owner):
    monkeypatch.setenv("WINDY_OWNER_IDS", f"{platform}:{owner}")
    other = "@stranger:example.test" if platform == "matrix" else "999"
    ok, out = _ask("/budget continue", platform, other)
    assert ok and "owner-only" in out and extra_today(db) == 0.0
    ok, out = _ask("/budget continue", platform, owner)
    assert ok and "keep going today" in out and extra_today(db) == 1.0


def test_an_agent_or_a_quoted_reply_cannot_continue(db, monkeypatch):
    monkeypatch.setenv("WINDY_OWNER_IDS", "matrix:@owner:example.test")
    ok, out = _ask("/budget continue", "matrix", "@agent_et26-ab12-cd34:chat.windychat.ai")
    assert ok and "owner-only" in out
    ok, _out = _ask("> <@owner:example.test> /budget continue\n\nsounds good", "matrix", "@stranger:example.test")
    assert extra_today(db) == 0.0
    ok, _out = _ask("/budget continue", "matrix", None)                   # no sender id at all
    assert extra_today(db) == 0.0


def test_the_version_command_shows_the_gate_limit_not_the_env(db, monkeypatch):
    monkeypatch.setenv("DAILY_BUDGET_USD", "99.00")
    CFG["costs"] = {"daily_budget_usd": 2.5}
    out = _say("/version")
    assert "Budget: $2.50/day" in out and "99" not in out


def test_the_cli_version_shows_the_gate_limit_not_the_env(monkeypatch, capsys):
    import argparse

    from windyfly.commands import _legacy

    monkeypatch.setenv("DAILY_BUDGET_USD", "99.00")
    monkeypatch.setattr("windyfly.config.load_config", lambda *a, **k: {"costs": {"daily_budget_usd": 3.25}})
    monkeypatch.setattr(_legacy, "can_run", lambda name: False)
    _legacy.cmd_version(argparse.Namespace())
    out = capsys.readouterr().out
    assert "Budget: $3.25/day" in out and "99.00" not in out


@pytest.mark.parametrize("value", ["0", "0.0", "-1"])
def test_budget_set_refuses_zero_and_below(db, value):
    """Hub on #601: /budget set 0 was accepted and then blocked every turn."""
    out = _say(f"/budget set {value}")
    assert "more than $0" in out and CFG["costs"]["daily_budget_usd"] == 1.0


@pytest.mark.parametrize("bad", [float("nan"), "lots", None])
def test_a_bad_daily_budget_in_the_config_falls_back_instead_of_blocking(db, bad):
    allowed = check_budget(db, {"costs": {"daily_budget_usd": bad}})
    assert allowed["allowed"] is True


def test_windy_version_reads_the_budget_from_the_config_it_is_given(tmp_path, monkeypatch, capsys):
    import argparse

    from windyfly.commands import _legacy

    cfg = tmp_path / "agent.toml"
    cfg.write_text("[costs]\ndaily_budget_usd = 7.25\n", encoding="utf-8")
    monkeypatch.delenv("WINDYFLY_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert _legacy._gate_daily_budget(str(cfg)) == 7.25
    assert _legacy._gate_daily_budget(str(tmp_path / "missing.toml")) == 5.0
    _legacy.cmd_version(argparse.Namespace(config=str(cfg)))
    assert "$7.25/day" in capsys.readouterr().out


def test_windy_version_accepts_config_on_the_command_line(monkeypatch):
    import sys

    from windyfly import cli
    from windyfly import commands as _commands

    seen = []
    monkeypatch.setattr(_commands, "cmd_version", lambda a: seen.append(a.config))
    monkeypatch.setattr(sys, "argv", ["windy", "version", "--config", "x.toml"])
    try:
        cli.main()
    except SystemExit as e:
        assert e.code in (0, None)
    assert seen == ["x.toml"]
