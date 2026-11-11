"""The Windy Text tool asks the agent's OWN do-not-text record before it sends (lean Fly step R2b).

Windy Legal (BOARD 2026-11-11T03:24:49Z): the direct Twilio channel may leave the agent only once every STOP is
honoured by the path that stays, proven by a count. The STOPs stay where they are (``windyfly.sms_optout``, the
table the channel writes to); the tool reads that same record before any request leaves. So the count is exact by
construction, and the last test here counts it anyway.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx
import pytest

from windyfly import sms_optout as so
from windyfly.memory.database import Database
from windyfly.tools import sms
from windyfly.tools.registry import ToolRegistry

class _Box(dict):
    """The record is opened only when a test first uses it, as the channel would: asking must not create it."""

    def __missing__(self, key):
        if key != "store":
            raise KeyError(key)
        self["store"] = so.OptOutStore(self["db"])
        return self["store"]


OWNER = "+15550001111"
STOPPED = "+15558888888"
FRIEND = "+15557777777"


@pytest.fixture
def box(monkeypatch, tmp_path):
    """The old (non-BYO) sender, switched on, with a file-backed memory and every number already approved."""
    monkeypatch.setenv("WINDY_TEXT_BASE_URL", "https://api.windytext.test")
    monkeypatch.setenv("WINDY_PASSPORT_EPT", "test_ept_jwt_value")
    monkeypatch.delenv("WINDY_TEXT_BYO", raising=False)
    monkeypatch.setenv("WINDY_OWNER_IDS", f"sms:{OWNER}")
    monkeypatch.setenv("WINDYFLY_AGENT_NAME", "Pip")
    monkeypatch.setenv("WINDY_OWNER_NAME", "Grant Whitmer")
    so._warned.clear()
    (tmp_path / "data").mkdir()
    db = Database(str(tmp_path / "data" / "mem.db"))
    monkeypatch.setattr(sms, "_pending", {})
    sms.register_sms_tools(ToolRegistry(), db, {})
    for n in (OWNER, STOPPED, FRIEND):
        sms._approve(n)
    calls: list[tuple[str, str]] = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(("POST", url))
        return httpx.Response(200, json={"sid": "SM1"}, request=httpx.Request("POST", url))

    def get(url, params=None, headers=None, timeout=None):
        calls.append(("GET", url))
        return httpx.Response(200, json={"approved": True, "opted_out": False}, request=httpx.Request("GET", url))

    monkeypatch.setattr(sms.httpx, "post", post)
    monkeypatch.setattr(sms.httpx, "get", get)
    yield _Box(db=db, calls=calls, path=tmp_path / "data" / "mem.db")
    monkeypatch.setattr(sms, "_db", None)
    monkeypatch.setattr(sms, "_config", None)
    db.close()


def _byo(monkeypatch):
    from windyfly.eternitas import agent_keys as ak

    monkeypatch.setenv("WINDY_TEXT_BYO", "1")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-own")
    monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
    monkeypatch.setattr(ak, "request_agent_token", lambda aud: {"token": f"tok-{aud}"})
    monkeypatch.setattr(ak, "service_dpop", lambda m, u: f"proof-{m}-{u}")


# ── a STOP in the agent's own record stops the tool ──

def test_a_number_that_texted_stop_is_refused_before_any_request_leaves(box):
    box["store"].record(STOPPED)
    out = sms.send_sms(STOPPED, "hello")
    assert out == {"status": "opted_out", "sent": False,
                   "error": "That number has opted out of texts (they replied STOP). I won't text it."}
    assert box["calls"] == []


def test_another_number_is_still_texted(box):
    box["store"].record(STOPPED)
    assert sms.send_sms(FRIEND, "hello")["status"] == "sent"
    assert [c[0] for c in box["calls"]] == ["POST"]


def test_the_owner_is_not_asked_to_approve_a_number_that_opted_out(box):
    box["store"].record("+15556660000")                     # never approved: the first-text question would come
    out = sms.send_sms("+15556660000", "hello")
    assert out["status"] == "opted_out" and "confirm_token" not in out
    assert sms._pending == {} and box["calls"] == []


def test_a_stop_between_the_question_and_the_owners_yes_wins(box):
    new = "+15556661111"
    asked = sms.send_sms(new, "hello")
    assert asked["status"] == "confirm_required"
    box["store"].record(new)                                 # the STOP arrives while the owner is deciding
    out = sms.confirm_sms(asked["confirm_token"])
    assert out["status"] == "opted_out" and box["calls"] == []
    assert not sms._is_approved(new)                         # and the yes approved nothing


def test_the_last_gate_holds_for_any_caller_of_the_sender(box):
    box["store"].record(STOPPED)
    assert sms._deliver("https://api.windytext.test", "ept", STOPPED, "hello")["status"] == "opted_out"
    assert box["calls"] == []


def test_start_makes_the_number_textable_again(box):
    box["store"].record(STOPPED)
    assert sms.send_sms(STOPPED, "hello")["status"] == "opted_out"
    box["store"].clear(STOPPED)
    assert sms.send_sms(STOPPED, "hello")["status"] == "sent"


def test_a_number_written_another_way_is_the_same_number(box):
    box["store"].record("+1 (555) 888-8888")
    assert sms.send_sms(STOPPED, "hello")["status"] == "opted_out" and box["calls"] == []


def test_a_plain_row_the_old_code_wrote_still_counts(box):
    box["db"].conn.executescript(so._SQL)
    box["db"].execute("INSERT INTO sms_optouts (number, opted_out_at) VALUES (?, ?)",
                      (so.number_key(STOPPED), "2026-11-01T00:00:00Z"))
    box["db"].commit()
    assert sms.send_sms(STOPPED, "hello")["status"] == "opted_out" and box["calls"] == []


# ── the owner's own Twilio account (BYO) ──

def test_byo_refuses_an_opted_out_number_without_asking_windy_text(box, monkeypatch):
    _byo(monkeypatch)
    box["store"].record(STOPPED)
    assert sms.send_sms(STOPPED, "hello")["status"] == "opted_out"
    assert box["calls"] == []                                # not even the consent lookup


def test_byo_texting_the_owner_is_never_refused_by_this_record(box, monkeypatch):
    _byo(monkeypatch)
    box["store"].record(STOPPED)
    so.key_path(box["path"]).unlink()                        # even with the key gone
    out = sms.send_sms(None, "hello")
    assert out["status"].startswith("sent") and [c[0] for c in box["calls"]] == ["GET", "POST"]


# ── the key is missing: only the owner ──

def _lose_the_key(box) -> None:
    box["store"].record(STOPPED)
    so.key_path(box["path"]).unlink()


def test_with_the_key_missing_every_number_but_the_owners_is_refused_in_legals_words(box):
    _lose_the_key(box)
    out = sms.send_sms(FRIEND, "hello")
    assert out == {"status": "failed", "sent": False, "error_code": "optout_key_missing",
                   "error": so.KEY_MISSING_NOTICE}
    assert box["calls"] == []


def test_with_the_key_missing_the_owner_is_still_texted(box):
    _lose_the_key(box)
    assert sms.send_sms(OWNER, "hello")["status"] == "sent"


def test_the_owner_named_only_in_the_config_counts_too(box, monkeypatch):
    monkeypatch.delenv("WINDY_OWNER_IDS")
    _lose_the_key(box)
    assert sms.send_sms(OWNER, "hello")["status"] == "failed"
    sms.register_sms_tools(ToolRegistry(), box["db"], {"trust": {"owner_ids": [f"sms:{OWNER}"]}})
    assert sms.send_sms(OWNER, "hello")["status"] == "sent"


def test_a_stop_kept_while_the_key_was_missing_is_honoured(box):
    _lose_the_key(box)
    store = so.OptOutStore(box["db"])                        # opens failed closed
    assert store.failed_closed
    store.record(OWNER)                                      # even the owner's own STOP: it goes to the pending list
    assert sms.send_sms(OWNER, "hello")["status"] == "opted_out" and box["calls"] == []


# ── when the record cannot be read, or there is none ──

def test_a_record_that_cannot_be_read_means_no_text(box, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(so, "state_readonly", boom)
    assert sms.send_sms(FRIEND, "hello")["status"] == "opted_out" and box["calls"] == []


def test_an_agent_with_no_record_sends_as_before(box):
    assert not so.key_path(box["path"]).exists()
    assert sms.send_sms(FRIEND, "hello")["status"] == "sent"
    assert not so.key_path(box["path"]).exists()             # asking wrote nothing: no key, no table
    assert box["db"].fetchone("SELECT 1 AS x FROM sqlite_master WHERE name = 'sms_optouts'") is None


def test_no_memory_wired_means_no_record_to_ask(box, monkeypatch):
    monkeypatch.setattr(sms, "_db", None)
    monkeypatch.setattr(sms, "_approved_mem", {FRIEND})
    assert sms.send_sms(FRIEND, "hello")["status"] == "sent"


# ── what is written down ──

def test_the_log_lines_hold_the_last_four_digits_only(box, caplog):
    box["store"].record(STOPPED)
    with caplog.at_level(logging.INFO, logger="windyfly.tools.sms"):
        sms.send_sms(STOPPED, "hello")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "8888" in text and STOPPED not in text and "5558888888" not in text


def test_the_tool_does_not_need_the_direct_channel():
    """So the channel can be cut without touching this check: the record, the owner rule and Legal's sentence all
    come from windyfly.sms_optout."""
    src = (Path(sms.__file__)).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+windyfly\.channels\.sms\b", src, re.M)
    assert "from windyfly.channels import sms" not in src
    from windyfly.channels import sms as chan

    assert chan.KEY_MISSING_NOTICE is so.KEY_MISSING_NOTICE and chan.owner_numbers is so.owner_numbers


# ── the count Legal asked for ──

def test_every_stop_in_the_record_is_honoured_by_the_tool_counted(box):
    numbers = [f"+1555200{i:04d}" for i in range(40)]
    for n in numbers:
        sms._approve(n)
    stopped = numbers[::2][:17] + numbers[35:]               # 17 + 5 = 22 of the 40
    for n in stopped:
        box["store"].record(n)
    in_record = box["db"].fetchone("SELECT COUNT(*) AS c FROM sms_optouts WHERE number NOT LIKE 'k:%'")["c"]
    results = {n: sms.send_sms(n, "hello")["status"] for n in numbers}
    refused = [n for n, s in results.items() if s == "opted_out"]
    assert in_record == len(stopped) == len(refused) == 22
    assert set(refused) == set(stopped)
    assert sum(1 for s in results.values() if s == "sent") == 18 == len(box["calls"])
