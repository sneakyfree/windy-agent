"""The suite never writes into the checkout (Hub on #700, 11-03): the conftest points every import-time path under
the project root at a per-test tmp dir, and fails the test that creates one of the known files in the checkout."""

from __future__ import annotations

from pathlib import Path

from tests import conftest

REPO = Path(conftest.__file__).resolve().parent.parent


def test_backup_state_and_the_audit_log_point_at_tmp_not_the_checkout(tmp_path):
    from windyfly import cloud_backup
    from windyfly.auth import audit

    for path in (cloud_backup._BACKUP_STATE_FILE, audit.AUDIT_LOG_PATH):
        assert REPO not in path.resolve().parents, path
        assert tmp_path in path.resolve().parents, path


def test_the_guard_watches_the_files_found_in_checkouts():
    assert {"data/windyfly.db", "data/backup_state.json", "data/audit"} <= set(conftest._NEVER_IN_CHECKOUT)
    assert conftest._REPO == REPO and (REPO / "pyproject.toml").is_file()
