"""The OAuth token cache and the offline queue live in the agent's state dir, private from the start
(P5.78.4, P5.104.5; Hub 10-12).

Both defaulted to paths relative to the working directory (data/.anthropic_oauth.json,
data/offline_queue.json), so where they landed depended on where the agent was started, and the
token cache was chmod-ed only after it was written. Now: WINDYFLY_OAUTH_CACHE /
WINDYFLY_OFFLINE_QUEUE, else the state dir (WINDY_STATE_DIR, default ~/.windy); written atomically
through a file created 0600 (os.open, never a chmod afterwards); and a file left at the old
cwd-relative place is moved once into the state dir. Fake values only.
"""

from __future__ import annotations

import logging
import os
import stat

import pytest

from windyfly import state_files
from windyfly.agent import oauth, offline

FAKE = '{"access_token": "fake-access", "refresh_token": "fake-refresh", "expires_at": 1}'


@pytest.fixture
def where(monkeypatch, tmp_path):
    state, cwd = tmp_path / "state", tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setenv("WINDY_STATE_DIR", str(state))
    monkeypatch.delenv("WINDYFLY_OAUTH_CACHE", raising=False)
    monkeypatch.delenv("WINDYFLY_OFFLINE_QUEUE", raising=False)
    monkeypatch.setattr(oauth, "_TOKEN_CACHE_PATH", None)
    monkeypatch.setattr(offline, "_QUEUE_PATH", None)
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(state_files, "get_project_root", lambda: tmp_path / "project")   # never the real repo
    old = os.umask(0)                      # the file must be 0600 by itself, not thanks to a umask
    yield state, cwd
    os.umask(old)


def _mode(p) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_the_oauth_cache_lives_in_the_state_dir_and_is_0600(where):
    state, cwd = where
    oauth.OAuthManager("fake-access", "fake-refresh", 1)._save_cache()
    cache = state / ".anthropic_oauth.json"
    assert cache.is_file() and _mode(cache) == 0o600
    assert not (cwd / "data").exists()


def test_the_oauth_cache_is_never_widened_then_chmod_ed(where, monkeypatch):
    state, _ = where
    monkeypatch.setattr(os, "chmod", lambda *a, **k: pytest.fail("created private, never chmod-ed after"))
    monkeypatch.setattr(type(state), "chmod", lambda *a, **k: pytest.fail("created private, never chmod-ed after"))
    oauth.OAuthManager("fake-access", "fake-refresh", 1)._save_cache()
    assert _mode(state / ".anthropic_oauth.json") == 0o600


def test_the_offline_queue_lives_in_the_state_dir_and_is_0600(where):
    state, cwd = where
    offline.queue_message("hello", "s1")
    q = state / "offline_queue.json"
    assert q.is_file() and _mode(q) == 0o600
    assert offline.get_queued_messages()[0]["message"] == "hello"
    assert not (cwd / "data").exists()


def test_an_explicit_path_is_still_honoured(where, monkeypatch, tmp_path):
    monkeypatch.setenv("WINDYFLY_OFFLINE_QUEUE", str(tmp_path / "elsewhere" / "q.json"))
    offline.queue_message("hello", "s1")
    assert (tmp_path / "elsewhere" / "q.json").is_file()


def test_a_cache_left_in_the_old_place_is_moved_once(where, caplog):
    state, cwd = where
    (cwd / "data").mkdir()
    (cwd / "data" / ".anthropic_oauth.json").write_text(FAKE, encoding="utf-8")
    with caplog.at_level(logging.INFO):
        path = oauth._cache_path()
    assert path == state / ".anthropic_oauth.json"
    assert path.read_text(encoding="utf-8") == FAKE and _mode(path) == 0o600
    assert not (cwd / "data" / ".anthropic_oauth.json").exists()
    assert "Moved the OAuth token cache" in caplog.text and "fake-access" not in caplog.text and "fake-refresh" not in caplog.text


def test_a_queue_left_in_the_old_place_is_moved_once(where):
    state, cwd = where
    (cwd / "data").mkdir()
    (cwd / "data" / "offline_queue.json").write_text('[{"message": "old", "session_id": "s"}]', encoding="utf-8")
    assert offline.get_queued_messages()[0]["message"] == "old"
    assert _mode(state / "offline_queue.json") == 0o600
    assert not (cwd / "data" / "offline_queue.json").exists()


def test_the_old_file_never_overwrites_a_newer_state_file(where):
    state, cwd = where
    state.mkdir()
    (state / ".anthropic_oauth.json").write_text('{"access_token": "newer"}', encoding="utf-8")
    (cwd / "data").mkdir()
    (cwd / "data" / ".anthropic_oauth.json").write_text(FAKE, encoding="utf-8")
    assert oauth._cache_path().read_text(encoding="utf-8") == '{"access_token": "newer"}'
    assert (cwd / "data" / ".anthropic_oauth.json").exists()      # left alone, not deleted


def test_an_old_file_under_the_project_root_is_found_too(where, tmp_path):
    state, _ = where
    (tmp_path / "project" / "data").mkdir(parents=True)
    (tmp_path / "project" / "data" / "offline_queue.json").write_text('[{"message": "root"}]', encoding="utf-8")
    assert offline.get_queued_messages()[0]["message"] == "root"
    assert _mode(state / "offline_queue.json") == 0o600
    assert not (tmp_path / "project" / "data" / "offline_queue.json").exists()


def test_the_move_happens_once_a_second_run_moves_and_logs_nothing(where, caplog):
    state, cwd = where
    (cwd / "data").mkdir()
    (cwd / "data" / "offline_queue.json").write_text('[{"message": "old", "session_id": "s"}]', encoding="utf-8")
    offline.get_queued_messages()                               # first run: the move
    caplog.clear()
    (cwd / "data" / "offline_queue.json").write_text("[]", encoding="utf-8")   # a stray old file again
    with caplog.at_level(logging.INFO):
        assert offline.get_queued_messages()[0]["message"] == "old"
    assert "Moved" not in caplog.text
    assert (cwd / "data" / "offline_queue.json").read_text(encoding="utf-8") == "[]"   # never moved over
