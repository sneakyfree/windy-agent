"""Tests for hatch actions."""

from __future__ import annotations

import pytest

from windyfly.hatch_actions import format_hatch_sms, send_hatch_sms


class TestFormatHatchSMS:
    def test_contains_agent_name(self):
        msg = format_hatch_sms("My Fly")
        assert "My Fly" in msg

    def test_contains_download_url(self):
        msg = format_hatch_sms("Fly", "https://custom.url/dl")
        assert "https://custom.url/dl" in msg

    def test_default_url(self):
        msg = format_hatch_sms("Fly")
        assert "windychat.com" in msg


class TestSendHatchSMS:
    async def test_mock_mode(self):
        """Without an SMS channel, should log and return mock_sent."""
        result = await send_hatch_sms("+15551234567", "Test Fly")
        assert result["status"] == "mock_sent"
        assert result["to"] == "+15551234567"
        assert "Test Fly" in result["message"]

    async def test_with_channel(self):
        """With a mock SMS channel, should call send_sms."""
        class MockSMS:
            def send_sms(self, to, msg):
                return {"status": "sent", "to": to}

        result = await send_hatch_sms("+15551234567", "Fly", sms_channel=MockSMS())
        assert result["status"] == "sent"

    async def test_channel_failure(self):
        """Channel errors should be caught, not raised."""
        class FailingSMS:
            def send_sms(self, to, msg):
                raise RuntimeError("Network error")

        result = await send_hatch_sms("+15551234567", "Fly", sms_channel=FailingSMS())
        assert result["status"] == "failed"
