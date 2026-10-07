"""Boss's C-C (10-07): ONE email to everybody (to/cc/bcc), ONE draft, ONE confirmation; and the
[TEST]-subject notice suppression."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from windyfly.agent import inbox_watch
from windyfly.channels.email import WindyMailAdapter
from windyfly.tools import mail

# ── normalize_recipients ────────────────────────────────────────


def test_strings_lists_names_and_duplicates():
    to, cc, bcc = mail.normalize_recipients(
        ["Ann <ann@x.com>", "bob@x.com; cy@x.com"], "ann@x.com, dee@x.com", ["eve@x.com", "BOB@x.com"])
    assert to == ["ann@x.com", "bob@x.com", "cy@x.com"]
    assert cc == ["dee@x.com"]           # ann is already on 'to'
    assert bcc == ["eve@x.com"]          # bob is already on 'to'


@pytest.mark.parametrize("bad", ["not-an-address", "a@b", "a@b.c\\nBcc: x@y.com", "a b@c.com", "<x>"])
def test_bad_addresses_are_refused_in_plain_words(bad):
    with pytest.raises(ValueError, match="doesn't look like an email address"):
        mail.normalize_recipients(bad)


def test_needs_a_to_and_has_a_sanity_cap():
    with pytest.raises(ValueError, match="No recipients"):
        mail.normalize_recipients("", cc="a@x.com")
    with pytest.raises(ValueError, match="more than"):
        mail.normalize_recipients([f"u{i}@x.com" for i in range(mail.MAX_RECIPIENTS + 1)])


# ── the send ────────────────────────────────────────────────────


@patch("windyfly.tools.mail._adapter")
def test_cc_and_bcc_travel_on_the_same_single_call(mock_adapter):
    adapter = MagicMock()
    adapter.send_email.return_value = {"status": "sent", "message_id": "m"}
    mock_adapter.return_value = adapter
    r = mail.send_email("a@x.com, b@x.com", "s", "body", cc="c@x.com", bcc=["d@x.com"])
    adapter.send_email.assert_called_once()
    args, kw = adapter.send_email.call_args
    assert args[0] == ["a@x.com", "b@x.com"] and kw == {"cc": ["c@x.com"], "bcc": ["d@x.com"]}
    assert r["recipients"] == {"to": 2, "cc": 1, "bcc": 1} and r["total"] == 4


@patch("windyfly.tools.mail._adapter")
def test_a_bad_address_sends_nothing(mock_adapter):
    adapter = MagicMock()
    mock_adapter.return_value = adapter
    r = mail.send_email("a@x.com, nope", "s", "b")
    assert r["status"] == "failed" and "nothing was sent" in r["error"]
    adapter.send_email.assert_not_called()


def test_the_tool_schema_offers_cc_and_bcc_and_says_one_email():
    from windyfly.tools.registry import ToolRegistry

    reg = ToolRegistry()
    mail.register_mail_tools(reg)
    fn = next(t["function"] for t in reg.get_schemas() if t["function"]["name"] == "send_email")
    assert {"to", "cc", "bcc"} <= set(fn["parameters"]["properties"])
    assert fn["parameters"]["required"] == ["to", "subject", "body"]
    assert "ONE" in fn["description"] and "never call this once per person" in fn["description"]


# ── ONE draft, ONE confirmation ─────────────────────────────────


@pytest.fixture
def _confirm(monkeypatch):
    monkeypatch.setenv("WINDY_SEND_CONFIRM", "1")
    mail._PENDING.clear()
    yield
    mail._PENDING.clear()


def test_one_draft_for_the_whole_batch_and_one_approval_sends_one_message(_confirm):
    sent = []

    class FakeAdapter:
        def send_email(self, to, subject, body, **kw):
            sent.append((to, kw))
            return {"status": "sent", "message_id": "m"}

    with patch.object(mail, "_adapter", return_value=FakeAdapter()):
        d = mail.send_email("a@x.com, b@x.com", "s", "b", cc="c@x.com")
        assert d["status"] == "pending_owner_approval" and d["to"] == "a@x.com, b@x.com" and d["cc"] == "c@x.com"
        assert len(mail.pending_drafts()) == 1 and sent == []
        res = mail.approve_latest("owner")
    assert res["status"] == "sent" and len(sent) == 1 and not mail.pending_drafts()
    assert sent[0][0] == ["a@x.com", "b@x.com"] and sent[0][1] == {"cc": ["c@x.com"], "bcc": None}


def test_a_bad_address_never_becomes_a_draft(_confirm):
    assert mail.send_email("nope", "s", "b")["status"] == "failed" and not mail.pending_drafts()


# ── the adapter's ONE /send ─────────────────────────────────────


def test_the_adapter_posts_one_request_with_the_lists(monkeypatch):
    monkeypatch.setenv("WINDYMAIL_EMAIL", "me@windyfly.ai")
    monkeypatch.setenv("WINDYMAIL_JMAP_TOKEN", "tok")
    posts = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posts.append((url, json))
        return httpx.Response(202, json={"message_id": "mid"}, request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("windyfly.trust.gate.require_trust_sync", lambda *a, **k: None)
    a = WindyMailAdapter()
    r = a.send_email(["a@x.com", "b@x.com"], "s", "body", cc=["c@x.com"], bcc=["d@x.com"])
    assert r == {"status": "sent", "message_id": "mid"} and len(posts) == 1
    assert posts[0][1]["to"] == ["a@x.com", "b@x.com"] and posts[0][1]["cc"] == ["c@x.com"] and posts[0][1]["bcc"] == ["d@x.com"]


def test_the_adapter_still_posts_a_single_to_as_a_one_item_list(monkeypatch):
    monkeypatch.setenv("WINDYMAIL_EMAIL", "me@windyfly.ai")
    monkeypatch.setenv("WINDYMAIL_JMAP_TOKEN", "tok")
    posts = []
    monkeypatch.setattr("httpx.post", lambda url, json=None, headers=None, timeout=None: posts.append(json) or
                        httpx.Response(200, json={}, request=httpx.Request("POST", url)))
    monkeypatch.setattr("windyfly.trust.gate.require_trust_sync", lambda *a, **k: None)
    WindyMailAdapter().send_email("a@x.com", "s", "b")
    assert posts[0]["to"] == ["a@x.com"] and "cc" not in posts[0] and "bcc" not in posts[0]


# ── [TEST] notices ──────────────────────────────────────────────


@pytest.mark.parametrize("subject", ["[TEST] seed", "  [test] x", "Re: [TEST] seed", "Fwd: RE: [Test] y"])
def test_test_marked_subjects(subject):
    assert inbox_watch.is_test_marked({"subject": subject})


@pytest.mark.parametrize("subject", ["Invoice [TEST] later", "TEST seed", "", "Hello"])
def test_ordinary_subjects_are_not_test_marked(subject):
    assert not inbox_watch.is_test_marked({"subject": subject})


def test_test_mail_is_seen_but_never_announced(tmp_path):
    class A:
        last_error = ""

        def __init__(self, msgs):
            self.msgs = msgs

        def check_inbox(self, unread_only=True):
            return self.msgs

    first = [{"id": "0", "subject": "old", "from": "x@y.z"}]
    inbox_watch.poll_new_messages(A(first), state_dir=tmp_path)          # seeds silently
    msgs = first + [{"id": "1", "subject": "[TEST] seed 1", "from": "a@b.c"},
                    {"id": "2", "subject": "Real", "from": "d@e.f"}]
    fresh = inbox_watch.poll_new_messages(A(msgs), state_dir=tmp_path)
    assert [m["id"] for m in fresh] == ["2"]
    assert inbox_watch.poll_new_messages(A(msgs), state_dir=tmp_path) == []   # the test mail stays seen


# ── Mail's refusals reach the owner as Mail's own sentence ──────


@pytest.mark.parametrize("status,body,expect", [
    (400, {"detail": "Too many recipients (12); tier limit is 10"}, "Too many recipients (12); tier limit is 10"),
    (422, {"detail": "Invalid recipient address: nope"}, "Invalid recipient address: nope"),
    (429, {"detail": "Daily send limit reached"}, "Daily send limit reached"),
    (403, {"error": "from_not_owned", "message": "You do not own that address"}, "You do not own that address"),
])
def test_the_adapter_returns_mails_own_sentence(monkeypatch, status, body, expect):
    monkeypatch.setenv("WINDYMAIL_EMAIL", "me@windyfly.ai")
    monkeypatch.setenv("WINDYMAIL_JMAP_TOKEN", "tok")
    monkeypatch.setattr("httpx.post", lambda url, **k: httpx.Response(status, json=body, request=httpx.Request("POST", url)))
    monkeypatch.setattr("windyfly.trust.gate.require_trust_sync", lambda *a, **k: None)
    r = WindyMailAdapter().send_email("a@x.com", "s", "b")
    assert r["status"] == "failed" and r["error"] == expect


def test_a_5xx_says_check_sent_before_resending(monkeypatch):
    monkeypatch.setenv("WINDYMAIL_EMAIL", "me@windyfly.ai")
    monkeypatch.setenv("WINDYMAIL_JMAP_TOKEN", "tok")
    monkeypatch.setattr("httpx.post", lambda url, **k: httpx.Response(502, text="bad gateway", request=httpx.Request("POST", url)))
    monkeypatch.setattr("windyfly.trust.gate.require_trust_sync", lambda *a, **k: None)
    r = WindyMailAdapter().send_email("a@x.com", "s", "b")
    assert r["status"] == "failed" and "Check the Sent folder" in r["error"]


@pytest.mark.parametrize("flag,must,must_not", [
    ("1", "do not ask for a yes in text first", "BEFORE you call this"),
    ("0", "get a yes in chat BEFORE you call this", "do not ask for a yes in text"),
    ("", "get a yes in chat BEFORE you call this", "do not ask for a yes in text"),
])
def test_the_description_is_honest_about_who_confirms(monkeypatch, flag, must, must_not):
    from windyfly.tools.registry import ToolRegistry

    if flag:
        monkeypatch.setenv("WINDY_SEND_CONFIRM", flag)
    else:
        monkeypatch.delenv("WINDY_SEND_CONFIRM", raising=False)
    reg = ToolRegistry()
    mail.register_mail_tools(reg)
    desc = next(t["function"]["description"] for t in reg.get_schemas() if t["function"]["name"] == "send_email")
    assert must in desc and must_not not in desc
    assert "never call this once per person" in desc and "ONE" in desc
