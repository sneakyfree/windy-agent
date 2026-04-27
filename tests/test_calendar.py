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


# ── setup_calendar_oauth + CLI dispatch (PR #79) ───────────────────


def test_setup_calendar_oauth_missing_creds_returns_false(tmp_path, monkeypatch):
    """If the OAuth credentials JSON isn't on disk, fail cleanly."""
    from windyfly.tools import calendar as calendar_module
    monkeypatch.setattr(calendar_module, "_CREDS_PATH", tmp_path / "missing.json")
    out = calendar_module.setup_calendar_oauth()
    assert out is False


def test_setup_calendar_oauth_writes_token_on_success(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from windyfly.tools import calendar as calendar_module

    creds = tmp_path / "creds.json"
    creds.write_text('{"installed": {}}')
    token = tmp_path / "token.json"
    monkeypatch.setattr(calendar_module, "_CREDS_PATH", creds)
    monkeypatch.setattr(calendar_module, "_TOKEN_PATH", token)

    fake_creds_obj = MagicMock()
    fake_creds_obj.to_json.return_value = '{"refresh_token": "test-rt"}'
    fake_flow = MagicMock()
    fake_flow.run_local_server.return_value = fake_creds_obj
    fake_module = MagicMock()
    fake_module.InstalledAppFlow.from_client_secrets_file.return_value = fake_flow
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "google_auth_oauthlib.flow", fake_module)

    out = calendar_module.setup_calendar_oauth()
    assert out is True
    assert token.exists()
    assert "refresh_token" in token.read_text()
    fake_module.InstalledAppFlow.from_client_secrets_file.assert_called_once_with(
        str(creds),
        scopes=["https://www.googleapis.com/auth/calendar"],
    )


def test_cli_dispatch_includes_setup_calendar():
    """Closes the pre-existing dead-end where graceful refusals
    referenced `windy setup-calendar` but the subcommand didn't exist."""
    import inspect
    import windyfly.cli as cli_module
    src = inspect.getsource(cli_module)
    assert '"setup-calendar": cmd_setup_calendar' in src, (
        "setup-calendar must be in the CLI dispatch table"
    )
    assert '"setup-calendar"' in src, (
        "setup-calendar must have an add_parser entry"
    )
