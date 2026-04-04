"""Tests for the calendar tool."""

from unittest.mock import MagicMock, patch

from windyfly.tools.calendar import (
    _is_configured,
    _not_configured_response,
    create_event,
    get_today_events,
    get_upcoming_events,
)


def test_not_configured():
    """When no token file exists, should give helpful message."""
    with patch("windyfly.tools.calendar._TOKEN_PATH") as mock_path:
        mock_path.exists.return_value = False
        result = get_today_events()
        assert "don't have access" in result["message"]
        assert "setup-calendar" in result["message"]


def test_not_configured_response():
    resp = _not_configured_response()
    assert resp["events"] == []
    assert "reminders" in resp["message"].lower()


def test_is_configured_false():
    with patch("windyfly.tools.calendar._TOKEN_PATH") as mock_path:
        mock_path.exists.return_value = False
        assert _is_configured() is False


def test_is_configured_true():
    with patch("windyfly.tools.calendar._TOKEN_PATH") as mock_path:
        mock_path.exists.return_value = True
        assert _is_configured() is True


def test_get_today_not_configured():
    with patch("windyfly.tools.calendar._is_configured", return_value=False):
        result = get_today_events()
        assert "don't have access" in result["message"]


def test_get_upcoming_not_configured():
    with patch("windyfly.tools.calendar._is_configured", return_value=False):
        result = get_upcoming_events(7)
        assert "don't have access" in result["message"]


def test_create_event_not_configured():
    with patch("windyfly.tools.calendar._is_configured", return_value=False):
        result = create_event("Meeting", "2026-04-05T14:00:00")
        assert "don't have access" in result["message"]


@patch("windyfly.tools.calendar._get_service")
@patch("windyfly.tools.calendar._is_configured", return_value=True)
def test_get_today_auth_failed(mock_conf, mock_svc):
    """When service returns None (auth failed), show error."""
    mock_svc.return_value = None
    result = get_today_events()
    assert "error" in result


@patch("windyfly.tools.calendar._get_service")
@patch("windyfly.tools.calendar._is_configured", return_value=True)
def test_create_event_success(mock_conf, mock_svc):
    """Mock successful event creation."""
    mock_service = MagicMock()
    mock_service.events().insert().execute.return_value = {
        "id": "event123",
        "htmlLink": "https://calendar.google.com/event/123",
    }
    mock_svc.return_value = mock_service

    result = create_event("Test Meeting", "2026-04-05T14:00:00")
    assert result["success"] is True
    assert result["event_id"] == "event123"
