"""Tests for hatch actions."""

from __future__ import annotations

import pytest

from windyfly.hatch_actions import format_hatch_sms, send_hatch_sms


class TestFormatHatchSMS:
    def test_contains_agent_name(self):
        msg = format_hatch_sms("My Fly")
        assert "My Fly" in msg

    def test_contains_dashboard_url(self):
        msg = format_hatch_sms("Fly", "https://custom.url/dashboard")
        assert "https://custom.url/dashboard" in msg

    def test_default_url(self):
        msg = format_hatch_sms("Fly")
        assert "windypro.thewindstorm.uk" in msg

    def test_its_alive(self):
        msg = format_hatch_sms("Fly")
        assert "ALIVE" in msg


class TestSendHatchSMS:
    async def test_mock_mode(self):
        """Without Twilio creds, should log and return mock_sent."""
        result = await send_hatch_sms("+15551234567", "Test Fly")
        assert result["status"] == "mock_sent"
        assert result["to"] == "+15551234567"
        assert "Test Fly" in result["message"]

    async def test_mock_mode_with_dashboard(self):
        """Should include dashboard URL in message."""
        result = await send_hatch_sms(
            "+15551234567", "Fly", dashboard_url="https://example.com"
        )
        assert result["status"] == "mock_sent"
        assert "https://example.com" in result["message"]
