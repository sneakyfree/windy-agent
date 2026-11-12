"""The burn-rate self-pause is ONE step (the Hub's note on #785, BOARD 2026-11-11T09:28:02Z): two channel processes that
see a breach in the same moment used to both pause and both tell the owner. Now exactly one caller pauses and tells."""

from __future__ import annotations

import json
import threading

import pytest

from windyfly.agent import spend_monitor as sm


@pytest.fixture(autouse=True)
def _flag(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_PAUSE_FLAG", str(tmp_path / "state" / ".paused"))
    monkeypatch.setattr(sm, "is_yolo_active", lambda: False)
    monkeypatch.setattr(sm, "_memory_pause", None)          # a fresh process each test; restored after
    yield tmp_path / "state" / ".paused"


def _breach(monkeypatch):
    monkeypatch.setattr(sm, "check_burn_threshold",
                        lambda db, threshold_usd_per_hour=None: {"breach": True, "current_hourly": 9.0,
                                                                   "threshold": 5.0, "by_provider": {}})


def test_the_first_caller_pauses_and_the_second_does_not(_flag):
    assert sm.pause_first("auto: one") is True
    assert sm.pause_first("auto: two") is False
    assert sm.is_paused()
    assert json.loads(_flag.read_text(encoding="utf-8"))["reason"] == "auto: one"     # the winner's flag stays


def test_no_temporary_file_is_left_beside_the_flag(_flag):
    sm.pause_first("auto: one")
    sm.pause_first("auto: two")
    assert sorted(p.name for p in _flag.parent.iterdir()) == [".paused"]


def test_two_processes_in_the_same_moment_give_one_pause_and_one_notice(monkeypatch):
    _breach(monkeypatch)
    start = threading.Barrier(8)
    results: list[str] = []
    lock = threading.Lock()

    def heartbeat():
        start.wait()
        r = sm.maybe_auto_pause(db=None)
        with lock:
            results.append(r["action"])

    threads = [threading.Thread(target=heartbeat) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("paused") == 1                 # one notice to the owner
    assert results.count("noop:already_paused") == 7


def test_an_existing_pause_by_the_owner_is_never_overwritten(_flag, monkeypatch):
    sm.pause(reason="manual", actor="user")
    _breach(monkeypatch)
    assert sm.maybe_auto_pause(db=None) == {"action": "noop:already_paused"}
    assert json.loads(_flag.read_text(encoding="utf-8"))["actor"] == "user"


def _unwritable(_flag):
    _flag.parent.mkdir(parents=True, exist_ok=True)
    _flag.parent.chmod(0o500)


def test_a_flag_that_cannot_be_written_stops_this_process_in_memory(_flag, monkeypatch):
    """Boss's note on #805: a failed write must still stop the spending, and the owner hears one true sentence."""
    _breach(monkeypatch)
    _unwritable(_flag)
    try:
        out = sm.maybe_auto_pause(db=None)
        assert out["action"] == "paused_here" and out["current_hourly"] == 9.0
        assert not _flag.exists()                     # nothing on disk ...
        assert sm.is_paused()                         # ... but this process makes no model call
        assert sm.pause_reason()["held"] == "memory"
        assert sm.maybe_auto_pause(db=None) == {"action": "noop:already_paused"}   # told once, not every check
    finally:
        _flag.parent.chmod(0o700)


def test_resume_here_starts_it_again(_flag, monkeypatch):
    _breach(monkeypatch)
    _unwritable(_flag)
    try:
        sm.maybe_auto_pause(db=None)
        assert sm.resume() == {"ok": True, "was_paused": True}
        assert not sm.is_paused()
    finally:
        _flag.parent.chmod(0o700)


def test_a_restart_forgets_it_and_the_next_check_stops_again(_flag, monkeypatch):
    _breach(monkeypatch)
    _unwritable(_flag)
    try:
        sm.maybe_auto_pause(db=None)
        monkeypatch.setattr(sm, "_memory_pause", None)          # what a restart does to a memory pause
        assert not sm.is_paused()
        assert sm.maybe_auto_pause(db=None)["action"] == "paused_here"   # still too fast, still unwritable
        assert sm.is_paused()
    finally:
        _flag.parent.chmod(0o700)


def test_the_owner_hears_legals_words_for_a_memory_pause_and_the_old_ones_otherwise():
    words = ("I've stopped here because I was spending faster than your limit allows. I couldn't save the pause "
             "everywhere, so I may still be running in your other chats. Send /resume here to start me again. If I "
             "restart, I'll check the spending again and stop if it's still too high.")
    assert sm.auto_pause_notice({"action": "paused_here", "current_hourly": 9.0, "threshold": 5.0}) == words
    old = sm.auto_pause_notice({"action": "paused", "current_hourly": 9.0, "threshold": 5.0})
    assert old.startswith("⚠️ I paused myself because I was spending too fast.") and words not in old
    assert set(sm.NOTICE_ACTIONS) == {"paused", "paused_here"}


def test_resume_then_a_new_breach_pauses_again(monkeypatch):
    _breach(monkeypatch)
    assert sm.maybe_auto_pause(db=None)["action"] == "paused"
    sm.resume()
    assert sm.maybe_auto_pause(db=None)["action"] == "paused"


def test_a_file_system_without_hard_links_still_pauses_once(_flag, monkeypatch):
    """A missed pause costs money: where os.link is refused, the flag is created exclusively instead."""
    import os

    def no_links(src, dst):
        raise PermissionError("no hard links here")

    monkeypatch.setattr(os, "link", no_links)
    assert sm.pause_first("auto: one") is True
    assert sm.pause_first("auto: two") is False
    assert sm.is_paused()
    assert json.loads(_flag.read_text(encoding="utf-8"))["reason"] == "auto: one"
    assert sorted(p.name for p in _flag.parent.iterdir()) == [".paused"]
