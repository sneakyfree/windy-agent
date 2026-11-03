"""One idempotency key per intended outward send (Boss 2026-11-02T22:13Z hard gate, part 2). The 11-02 lab restart
re-ran an owner's "send" and mailed twice; with a key, Windy Mail (#275) and Windy Text (#125) answer a repeat with
the first result and never send again."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from windyfly.agent import action_key as ak

EVENT = "$owner-send:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _passport(monkeypatch):
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-86FT")


def test_no_trigger_means_no_key():
    assert ak.key("mail.send", "a@b.c") == "" and ak.headers("mail.send", "a@b.c") == {}


def _turn(event: str, *sends: tuple[str, ...]) -> list[str]:
    """One turn answering ``event``: the keys of its sends, in order."""
    tok = ak.enter(event)
    try:
        return [ak.key(*s) for s in sends]
    finally:
        ak.leave(tok)


def test_a_re_run_of_the_same_turn_gets_the_same_keys():
    first = _turn(EVENT, ("mail.send", "a@b.c", "hi"), ("sms.send", "+15551234567"))
    assert first == _turn(EVENT, ("mail.send", "a@b.c", "hi"), ("sms.send", "+15551234567"))
    assert all(len(k) == 64 and all(c in "0123456789abcdef" for c in k) for k in first)  # fits Mail + Text
    assert _turn("$another-message", ("mail.send", "a@b.c", "hi"))[0] != first[0]
    assert ak.trigger() == ""


def test_two_sends_to_one_target_in_one_turn_get_two_keys_and_a_re_run_the_same_two():
    """Hub on #693: "text Mom I'm late, and text her the address" is two texts, so two keys."""
    two = _turn(EVENT, ("sms.send", "+15551234567"), ("sms.send", "+15551234567"))
    assert two[0] != two[1]
    assert two == _turn(EVENT, ("sms.send", "+15551234567"), ("sms.send", "+15551234567"))


def test_different_targets_get_different_keys():
    a, b, c = _turn(EVENT, ("mail.send", "a@b.c", "hi"), ("mail.send", "x@y.z", "hi"), ("sms.send", "a@b.c", "hi"))
    assert len({a, b, c}) == 3


@pytest.mark.asyncio
async def test_the_turn_thread_sees_the_trigger():
    from windyfly.agent.executor import run_turn

    tok = ak.enter(EVENT)
    try:
        assert await run_turn(ak.trigger) == EVENT
    finally:
        ak.leave(tok)
    assert await run_turn(ak.trigger) == ""


@pytest.mark.asyncio
async def test_matrix_sets_the_trigger_per_message_and_clears_it():
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot

    seen = []
    bot = WindyFlyMatrixBot.__new__(WindyFlyMatrixBot)
    bot._on_message_inner = AsyncMock(side_effect=lambda r, e: seen.append(ak.trigger()))
    ev = MagicMock()
    ev.event_id = EVENT
    await bot._on_message(MagicMock(), ev)
    assert seen == [EVENT] and ak.trigger() == ""


# ── Windy Mail ───────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mail(monkeypatch):
    from windyfly.channels import email

    monkeypatch.setenv("WINDYMAIL_EMAIL", "lab@windymail.ai")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-tok")
    monkeypatch.setattr(email.time, "sleep", lambda s: None)
    a = email.WindyMailAdapter()
    a.db = None
    return a


def _post_answers(monkeypatch, answers):
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(dict(headers or {}))
        status, body, hdrs = answers[min(len(calls), len(answers)) - 1]
        return httpx.Response(status, json=body, headers=hdrs, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    return calls


def test_every_mail_send_carries_its_key_and_a_re_run_carries_the_same(mail, monkeypatch):
    calls = _post_answers(monkeypatch, [(202, {"message_id": "m1"}, {})])
    for _ in range(2):  # the same turn run twice (the 11-02 restart replay)
        tok = ak.enter(EVENT)
        try:
            mail.send_email("rig@windymail.ai", "Lab", "hello")
        finally:
            ak.leave(tok)
    keys = [c.get("Idempotency-Key") for c in calls]
    assert keys[0] and keys[0] == keys[1]


def test_a_reused_key_refusal_is_one_plain_sentence(mail, monkeypatch):
    _post_answers(monkeypatch, [(422, {"error": "idempotency_key_reused", "message": "raw server words"}, {})])
    tok = ak.enter(EVENT)
    try:
        out = mail.send_email("rig@windymail.ai", "Lab", "hello")
    finally:
        ak.leave(tok)
    assert out == {"status": "failed", "error": "The mail server refused it."}


def test_without_a_trigger_mail_sends_as_before(mail, monkeypatch):
    calls = _post_answers(monkeypatch, [(202, {"message_id": "m1"}, {})])
    assert mail.send_email("rig@windymail.ai", "Lab", "hello")["status"] == "sent"
    assert "Idempotency-Key" not in calls[0]


def test_a_mail_replay_is_reported_sent_and_not_sent_again(mail, monkeypatch):
    _post_answers(monkeypatch, [(200, {"message_id": "m1", "status": "sent"}, {"Idempotent-Replayed": "true"})])
    tok = ak.enter(EVENT)
    try:
        out = mail.send_email("rig@windymail.ai", "Lab", "hello")
    finally:
        ak.leave(tok)
    assert out == {"status": "sent", "message_id": "m1", "replayed": True}


def test_mail_in_progress_asks_once_more_with_the_same_key(mail, monkeypatch):
    calls = _post_answers(monkeypatch, [(409, {"error": "idempotency_in_progress", "message": "wait"}, {}),
                                        (202, {"message_id": "m1"}, {})])
    tok = ak.enter(EVENT)
    try:
        assert mail.send_email("rig@windymail.ai", "Lab", "hello")["status"] == "sent"
    finally:
        ak.leave(tok)
    assert len(calls) == 2 and calls[0]["Idempotency-Key"] == calls[1]["Idempotency-Key"]


def test_mail_outcome_unknown_is_never_resent(mail, monkeypatch):
    """Boss 22:24Z: outcome unknown = never resent; the owner is told to check Sent (the existing sentence)."""
    calls = _post_answers(monkeypatch, [(409, {"error": "idempotency_outcome_unknown", "message": "x"}, {})])
    tok = ak.enter(EVENT)
    try:
        out = mail.send_email("rig@windymail.ai", "Lab", "hello")
    finally:
        ak.leave(tok)
    assert len(calls) == 1 and out["status"] == "failed" and "Check the Sent folder" in out["error"]


# ── Windy Text ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_every_text_carries_its_key_and_in_progress_asks_once_more(monkeypatch):
    from windyfly.tools import sms

    monkeypatch.setattr(sms.time, "sleep", lambda s: None)
    seen = []
    answers = [(409, {"error": "send_in_progress"}), (200, {"sid": "S1", "to": "+15551234567"})]

    def post(url, headers=None, json=None, timeout=None):
        seen.append(dict(headers or {}))
        status, body = answers[min(len(seen), len(answers)) - 1]
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(sms.httpx, "post", post)
    tok = ak.enter(EVENT)
    try:
        out = sms._deliver("https://text.test", "ept", "+15551234567", "hi")
    finally:
        ak.leave(tok)
    assert out["status"] == "sent" and len(seen) == 2
    assert seen[0]["Idempotency-Key"] and seen[0]["Idempotency-Key"] == seen[1]["Idempotency-Key"]


def test_without_a_trigger_a_text_has_no_key(monkeypatch):
    from windyfly.tools import sms

    seen = []

    def post(url, headers=None, json=None, timeout=None):
        seen.append(dict(headers or {}))
        return httpx.Response(200, json={"sid": "S1"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(sms.httpx, "post", post)
    sms._deliver("https://text.test", "ept", "+15551234567", "hi")
    assert "Idempotency-Key" not in seen[0]
    assert time  # (imported for the sleep patches above)


@pytest.mark.parametrize("body", [{"error": "idempotency_key_reused", "message": "raw server words"},
                                  {"detail": "idempotency_key_reused"}])  # Text #125: FastAPI's {"detail": code}
def test_a_reused_key_text_refusal_is_one_plain_sentence(monkeypatch, body):
    from windyfly.tools import sms

    def post(url, headers=None, json=None, timeout=None):
        return httpx.Response(422, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(sms.httpx, "post", post)
    out = sms._deliver("https://text.test", "ept", "+15551234567", "hi")
    assert out["error"] == sms.refusal_sentence(422, None) and out["error_code"] == "idempotency_key_reused"


@pytest.mark.asyncio
async def test_a_file_message_is_a_trigger_too():
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot

    seen = []
    bot = WindyFlyMatrixBot.__new__(WindyFlyMatrixBot)
    bot._on_file_inner = AsyncMock(side_effect=lambda r, e: seen.append(ak.trigger()))
    ev = MagicMock()
    ev.event_id = "$a-file"
    await bot._on_file(MagicMock(), ev)
    assert seen == ["$a-file"] and ak.trigger() == ""
