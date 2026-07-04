"""Tests for the update system."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from windyfly.update import (
    CACHE_FILE,
    apply_update,
    check_for_update,
    get_installed_version,
    get_latest_version,
    get_previous_version,
    is_newer,
    rollback,
)


@pytest.fixture(autouse=True)
def _isolate_update_history(monkeypatch, tmp_path):
    """apply_update records rollback history under windy_state_dir();
    keep it out of the real ~/.windy during tests."""
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))


def test_is_newer():
    assert is_newer("0.6.0", "0.5.1") is True
    assert is_newer("0.5.1", "0.5.1") is False
    assert is_newer("0.5.0", "0.5.1") is False
    assert is_newer("1.0.0", "0.9.9") is True
    assert is_newer("0.5.2", "0.5.1") is True


def test_is_newer_handles_bad_input():
    assert is_newer("bad", "0.5.1") is False
    assert is_newer("", "0.5.1") is False
    assert is_newer("v1.0.0", "0.5.1") is True  # handles 'v' prefix


def test_get_installed_version():
    from windyfly import __version__
    assert get_installed_version() == __version__


@patch("windyfly.update.httpx.get")
def test_get_latest_version_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"info": {"version": "0.6.0"}}
    mock_get.return_value = mock_resp
    assert get_latest_version() == "0.6.0"


@patch("windyfly.update.httpx.get")
def test_get_latest_version_failure(mock_get):
    mock_get.side_effect = Exception("network error")
    assert get_latest_version() is None


@patch("windyfly.update.httpx.get")
def test_check_for_update_available(mock_get, tmp_path):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"info": {"version": "99.0.0"}}
    mock_get.return_value = mock_resp

    with patch("windyfly.update.CACHE_FILE", tmp_path / ".update_check"):
        result = check_for_update(force=True)
        assert result is not None
        assert result["update_available"] is True
        assert result["latest"] == "99.0.0"


@patch("windyfly.update.httpx.get")
def test_check_for_update_already_latest(mock_get, tmp_path):
    from windyfly import __version__
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"info": {"version": __version__}}
    mock_get.return_value = mock_resp

    with patch("windyfly.update.CACHE_FILE", tmp_path / ".update_check"):
        result = check_for_update(force=True)
        assert result is None


def test_check_uses_cache(tmp_path):
    cache_file = tmp_path / ".update_check"
    cache_data = {
        "current": "0.5.1",
        "latest": "0.6.0",
        "update_available": True,
        "checked_at": time.time(),  # Fresh cache
    }
    cache_file.write_text(json.dumps(cache_data))

    with patch("windyfly.update.CACHE_FILE", cache_file):
        with patch("windyfly.update.httpx.get") as mock_get:
            result = check_for_update(force=False)
            mock_get.assert_not_called()
            assert result["update_available"] is True


def test_check_ignores_stale_cache(tmp_path):
    cache_file = tmp_path / ".update_check"
    cache_data = {
        "current": "0.5.1",
        "latest": "0.6.0",
        "update_available": True,
        "checked_at": time.time() - 100_000,  # Stale cache
    }
    cache_file.write_text(json.dumps(cache_data))

    with patch("windyfly.update.CACHE_FILE", cache_file):
        with patch("windyfly.update.httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"info": {"version": "99.0.0"}}
            mock_get.return_value = mock_resp

            result = check_for_update(force=False)
            mock_get.assert_called_once()
            assert result["latest"] == "99.0.0"


@patch("windyfly.update.subprocess.run")
def test_apply_update_success(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)
    with patch("windyfly.update.CACHE_FILE", tmp_path / ".update_check"):
        success, msg = apply_update()
        assert success is True
        assert "Updated" in msg


@patch("windyfly.update.subprocess.run")
def test_apply_update_failure(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stderr="some error")
    success, msg = apply_update()
    assert success is False
    assert "failed" in msg.lower()


@patch("windyfly.update.subprocess.run")
def test_apply_update_with_version(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)
    with patch("windyfly.update.CACHE_FILE", tmp_path / ".update_check"):
        success, msg = apply_update(target_version="0.4.0")
        assert success is True
        # Verify pip (the FIRST subprocess call; the second is the
        # post-install import verification) was pinned to the version
        call_args = mock_run.call_args_list[0][0][0]
        assert "windyfly==0.4.0" in call_args


def test_is_newer_major_bump():
    assert is_newer("2.0.0", "1.99.99") is True


def test_is_newer_partial_version():
    """Handles versions with only major.minor (no patch)."""
    assert is_newer("1.0", "0.5.1") is True  # (1, 0) > (0, 5, 1) via tuple comparison
    assert is_newer("0.5", "0.5.1") is False  # (0, 5) < (0, 5, 1)


# ── 2026-07-04 update-safety contract ──────────────────────────────


def test_is_newer_prerelease_ordering():
    """Pre-releases sort before their own final release, after
    everything below — previously any suffix meant 'not newer'."""
    assert is_newer("1.0.0rc1", "0.9.9") is True
    assert is_newer("1.0.0", "1.0.0rc1") is True
    assert is_newer("1.0.0rc1", "1.0.0") is False
    assert is_newer("1.0.0rc2", "1.0.0rc1") is True


def test_update_history_round_trip():
    from windyfly.update import record_update

    assert get_previous_version() is None
    record_update("0.5.1", "0.6.0")
    record_update("0.6.0", "0.7.0")
    assert get_previous_version() == "0.6.0"


def test_rollback_without_version_uses_history():
    from windyfly.update import record_update

    record_update("0.5.1", "0.6.0")
    with patch("windyfly.update._pip_install") as mock_pip, \
         patch("windyfly.update.verify_install") as mock_verify:
        mock_pip.return_value = MagicMock(returncode=0)
        mock_verify.return_value = (True, "0.5.1")
        success, msg = rollback()
    assert success is True
    mock_pip.assert_called_with("windyfly==0.5.1")


def test_rollback_without_version_or_history_errors():
    success, msg = rollback()
    assert success is False
    assert "No previous version recorded" in msg


def test_apply_update_auto_rolls_back_on_broken_install():
    """A release that installs but can't import must not brick the
    agent: apply_update rolls back to the recorded prior version."""
    with patch("windyfly.update._pip_install") as mock_pip, \
         patch("windyfly.update.verify_install") as mock_verify:
        mock_pip.return_value = MagicMock(returncode=0)
        mock_verify.return_value = (False, "ImportError: boom")
        success, msg = apply_update(target_version="9.9.9")
    assert success is False
    assert "rolled back" in msg.lower()
    # second pip call must pin the prior (currently running) version
    from windyfly import __version__
    assert mock_pip.call_args_list[-1][0][0] == f"windyfly=={__version__}"
