"""Inbox watch: the agent notices mail addressed to it (2026-09-05)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


from windyfly.agent import inbox_watch as iw
from windyfly.agent.maintenance import run_due_jobs


class FakeAdapter:
    def __init__(self, messages, last_error=""):
        self.messages = messages
        self.last_error = last_error
        self.calls = 0

    def check_inbox(self, unread_only=True):
        self.calls += 1
        return list(self.messages)


def _msgs(*ids):
    return [{"id": i, "from": f"{i}@example.com", "subject": f"Subject {i}"} for i in ids]


class TestPoll:
    def test_first_poll_seeds_silently(self, tmp_path):
        adapter = FakeAdapter(_msgs("a", "b"))
        assert iw.poll_new_messages(adapter, state_dir=tmp_path) == []
        state = json.loads((tmp_path / iw.STATE_FILE).read_text())
        assert state["seen"] == ["a", "b"]
        assert "last_poll" in state

    def test_second_poll_returns_only_new(self, tmp_path):
        adapter = FakeAdapter(_msgs("a"))
        iw.poll_new_messages(adapter, state_dir=tmp_path)
        adapter.messages = _msgs("a", "b", "c")
        fresh = iw.poll_new_messages(adapter, state_dir=tmp_path)
        assert [m["id"] for m in fresh] == ["b", "c"]
        # and a third poll with nothing new is quiet
        assert iw.poll_new_messages(adapter, state_dir=tmp_path) == []

    def test_seen_set_is_bounded(self, tmp_path):
        adapter = FakeAdapter([])
        iw.poll_new_messages(adapter, state_dir=tmp_path)
        adapter.messages = _msgs(*[f"m{i}" for i in range(iw.MAX_SEEN + 50)])
        iw.poll_new_messages(adapter, state_dir=tmp_path)
        state = json.loads((tmp_path / iw.STATE_FILE).read_text())
        assert len(state["seen"]) == iw.MAX_SEEN

    def test_message_key_falls_back_to_hash(self):
        m = {"from": "x@y", "subject": "hi", "date": "2026-09-05"}
        k1 = iw.message_key(m)
        assert k1.startswith("h:") and k1 == iw.message_key(dict(m))
        assert iw.message_key({"message_id": "<abc>"}) == "<abc>"

    def test_last_error_recorded(self, tmp_path):
        adapter = FakeAdapter([], last_error="inbox timed out after 15s")
        iw.poll_new_messages(adapter, state_dir=tmp_path)
        state = json.loads((tmp_path / iw.STATE_FILE).read_text())
        assert "timed out" in state["last_error"]


class TestNotice:
    def test_format_caps_and_names_sender(self):
        text = iw.format_notice(_msgs(*"abcdefg"), agent_name="Sunny")
        assert text.startswith("📬 New mail for Sunny: 7 message(s)")
        assert "a@example.com — Subject a" in text
        assert "… and 2 more" in text
        assert "f@example.com" not in text  # capped at MAX_NOTIFY_PER_TICK

    def test_format_handles_dict_and_list_senders(self):
        text = iw.format_notice([
            {"id": 1, "from": {"email": "d@x"}, "subject": "S1"},
            {"id": 2, "from": [{"email": "l@x"}], "subject": "S2"},
            {"id": 3},
        ])
        assert "d@x — S1" in text and "l@x — S2" in text
        assert "unknown sender — (no subject)" in text


class TestJob:
    def test_job_notifies_only_on_new_mail(self, tmp_path):
        adapter = FakeAdapter(_msgs("a"))
        sent = []
        job = iw.make_inbox_watch_job(
            sent.append, adapter_factory=lambda: adapter, state_dir=tmp_path,
            interval_s=1, agent_name="Sunny",
        )
        assert job.name == "inbox_watch"
        job.run()                      # seed: silent
        assert sent == []
        adapter.messages = _msgs("a", "b")
        job.run()
        assert len(sent) == 1 and "b@example.com" in sent[0]
        job.run()                      # nothing new
        assert len(sent) == 1

    def test_job_is_quiet_without_a_mailbox(self, tmp_path):
        sent = []
        job = iw.make_inbox_watch_job(sent.append, adapter_factory=lambda: None,
                                      state_dir=tmp_path)
        job.run()
        assert sent == [] and not (tmp_path / iw.STATE_FILE).exists()

    def test_job_never_raises(self, tmp_path):
        boom = MagicMock()
        boom.check_inbox.side_effect = RuntimeError("mail down")
        job = iw.make_inbox_watch_job(lambda t: None, adapter_factory=lambda: boom,
                                      state_dir=tmp_path)
        job.run()  # must not raise
        notify_boom = MagicMock(side_effect=RuntimeError("telegram down"))
        adapter = FakeAdapter(_msgs("a"))
        job2 = iw.make_inbox_watch_job(notify_boom, adapter_factory=lambda: adapter,
                                       state_dir=tmp_path / "b")
        job2.run()
        adapter.messages = _msgs("a", "b")
        job2.run()  # notify raises inside; the job must swallow it
        assert notify_boom.called

    def test_job_runs_on_the_maintenance_cadence(self, tmp_path):
        adapter = FakeAdapter(_msgs("a"))
        job = iw.make_inbox_watch_job(lambda t: None, adapter_factory=lambda: adapter,
                                      state_dir=tmp_path, interval_s=300)
        now = datetime(2026, 9, 5, 16, 0, tzinfo=timezone.utc)
        with patch.dict("os.environ", {"WINDY_STATE_DIR": str(tmp_path)}):
            assert run_due_jobs([job], now=now, state_dir=tmp_path) == ["inbox_watch"]
            assert run_due_jobs([job], now=now, state_dir=tmp_path) == []
        assert adapter.calls == 1


class TestListInboxSurfacesErrors:
    def test_list_inbox_reports_timeout_instead_of_empty(self):
        from windyfly.tools import mail as mail_tools

        adapter = FakeAdapter([], last_error="inbox timed out after 15s")
        with patch.object(mail_tools, "_adapter", return_value=adapter):
            out = mail_tools.list_inbox()
        assert out["status"] == "error"
        assert "timed out" in out["error"]
        assert out["messages"] == []

    def test_list_inbox_plain_when_ok(self):
        from windyfly.tools import mail as mail_tools

        adapter = FakeAdapter(_msgs("a"))
        with patch.object(mail_tools, "_adapter", return_value=adapter):
            out = mail_tools.list_inbox()
        assert "status" not in out and out["count"] == 1


class TestAdapterTimeout:
    def test_timeout_env_and_last_error(self):
        import httpx

        from windyfly.channels.email import WindyMailAdapter

        with patch.dict("os.environ", {"WINDYMAIL_EMAIL": "a@windymail.ai",
                                       "WINDYMAIL_JMAP_TOKEN": "t",
                                       "WINDYMAIL_TIMEOUT_S": "3"}):
            adapter = WindyMailAdapter()
        assert adapter.timeout_s == 3.0
        with patch("httpx.get", side_effect=httpx.ReadTimeout("slow")):
            assert adapter.check_inbox() == []
        assert adapter.last_error == "inbox timed out after 3s"
        ok = MagicMock(status_code=200)
        ok.json.return_value = {"messages": [{"id": "x"}]}
        with patch("httpx.get", return_value=ok):
            assert adapter.check_inbox() == [{"id": "x"}]
        assert adapter.last_error == ""
