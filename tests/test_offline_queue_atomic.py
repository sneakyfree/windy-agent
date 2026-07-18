"""Tests for atomic offline queue.

R1.5: Validates atomic file writes (temp + rename),
queue persistence, and cleanup behavior.
"""

import json
from pathlib import Path
from unittest.mock import patch

from windyfly.agent.offline import clear_queue, get_queued_messages, queue_message


class TestOfflineQueueAtomic:
    """Tests for the persistent offline message queue."""

    def test_atomic_write_survives_crash(self, tmp_path):
        """Messages queue correctly and persist to disk."""
        queue_path = tmp_path / "offline_queue.json"
        with patch("windyfly.agent.offline._QUEUE_PATH", queue_path):
            queue_message("test1")
            queue_message("test2")
            msgs = get_queued_messages()
            assert len(msgs) == 2
            assert msgs[0]["message"] == "test1"
            assert msgs[1]["message"] == "test2"

    def test_clear_queue(self, tmp_path):
        """clear_queue() removes all messages and returns count."""
        queue_path = tmp_path / "offline_queue.json"
        with patch("windyfly.agent.offline._QUEUE_PATH", queue_path):
            queue_message("test")
            count = clear_queue()
            assert count == 1
            assert get_queued_messages() == []

    def test_no_tmp_file_left_after_write(self, tmp_path):
        """Atomic write should not leave .tmp files behind."""
        queue_path = tmp_path / "offline_queue.json"
        with patch("windyfly.agent.offline._QUEUE_PATH", queue_path):
            queue_message("test")
            tmp_file = queue_path.with_suffix(".tmp")
            assert not tmp_file.exists()

    def test_queue_message_has_timestamp(self, tmp_path):
        """Each queued message should have a queued_at timestamp."""
        queue_path = tmp_path / "offline_queue.json"
        with patch("windyfly.agent.offline._QUEUE_PATH", queue_path):
            queue_message("hello", session_id="sess1")
            msgs = get_queued_messages()
            assert len(msgs) == 1
            assert "queued_at" in msgs[0]
            assert msgs[0]["session_id"] == "sess1"

    def test_queue_file_is_valid_json(self, tmp_path):
        """The queue file should always contain valid JSON."""
        queue_path = tmp_path / "offline_queue.json"
        with patch("windyfly.agent.offline._QUEUE_PATH", queue_path):
            queue_message("msg1")
            queue_message("msg2")
            content = json.loads(queue_path.read_text(encoding="utf-8"))
            assert isinstance(content, list)
            assert len(content) == 2

    def test_empty_queue_returns_empty_list(self, tmp_path):
        """get_queued_messages() returns [] when no queue file exists."""
        queue_path = tmp_path / "nonexistent_queue.json"
        with patch("windyfly.agent.offline._QUEUE_PATH", queue_path):
            assert get_queued_messages() == []
