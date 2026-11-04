"""Old credentials / env-file backups are pruned (Hub on #714; rule OK'd by Hub 11-04).

credentials.json.bak-*: deleted only when older than 30 days AND every kid in it is in the current file or NOT
active on the live public key list; unreadable list = nothing that needs it is deleted; the newest is always kept;
an unparseable one is kept. Deleting = zeros + fsync, then unlink. Env-file copies (.bak-ept-, .bak-deregister-):
0600, newest of each kind kept, older than 30 days deleted. Logs carry counts, never a kid.
"""
from __future__ import annotations

import calendar
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

from windyfly.eternitas import agent_keys as ak

NOW = calendar.timegm(time.strptime("20261204T000000Z", "%Y%m%dT%H%M%SZ"))
OLD = "20261001T000000Z"      # > 30 days before NOW
RECENT = "20261201T000000Z"   # < 30 days


def _creds(kids: list[str], active: str | None = None) -> dict:
    return {"eternitas": {"kid": active or (kids[-1] if kids else None),
                          "keys": [{"kid": k, "private_key": f"PEM-{k}", "status": "active"} for k in kids]}}


@pytest.fixture
def box(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps(_creds(["kid-now"])))
    monkeypatch.setattr(ak, "current_passport", lambda: "ET26-TEST-PRUN")
    state = {"active": {"kid-now"}, "list_status": 200, "gets": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["gets"] += 1
        if state["list_status"] != 200:
            return httpx.Response(state["list_status"], json={"detail": "down"})
        keys = [{"kid": k, "status": "active"} for k in state["active"]] + [{"kid": "kid-gone", "status": "retired"}]
        return httpx.Response(200, json={"keys": keys})

    state["transport"] = httpx.MockTransport(handler)
    return path, state


def _bak(path: Path, stamp: str, kids: list[str] | None, raw: str | None = None) -> Path:
    b = path.with_name(f"{path.name}.bak-{stamp}")
    b.write_text(raw if raw is not None else json.dumps(_creds(kids or [])))
    return b


def _prune(path, state):
    return ak.prune_key_backups(transport=state["transport"], path=path, now=NOW)


def test_an_old_backup_of_only_current_or_retired_keys_is_deleted(box):
    path, state = box
    gone = _bak(path, OLD, ["kid-gone"])
    same = _bak(path, "20261002T000000Z", ["kid-now"])
    newest = _bak(path, RECENT, ["kid-gone"])
    out = _prune(path, state)
    assert not gone.exists() and not same.exists() and newest.exists()
    assert out["deleted"] == 2


def test_an_old_backup_holding_a_key_still_active_is_kept(box):
    path, state = box
    state["active"] = {"kid-now", "kid-other"}
    keep = _bak(path, OLD, ["kid-other"])
    _bak(path, RECENT, ["kid-now"])
    out = _prune(path, state)
    assert keep.exists() and out["kept_active_key"] == 1


@pytest.mark.parametrize("status", [500, 503, 404])
def test_an_unreadable_key_list_deletes_nothing_that_needs_it(box, status):
    path, state = box
    state["list_status"] = status
    needs = _bak(path, OLD, ["kid-gone"])
    current_only = _bak(path, "20261002T000000Z", ["kid-now"])
    _bak(path, RECENT, ["kid-now"])
    out = _prune(path, state)
    assert needs.exists() and out["kept_list_unknown"] == 1
    assert not current_only.exists()          # in the current file: no list needed


def test_no_list_read_when_every_old_kid_is_current(box):
    path, state = box
    _bak(path, OLD, ["kid-now"])
    _bak(path, RECENT, ["kid-now"])
    _prune(path, state)
    assert state["gets"] == 0


def test_the_newest_backup_is_kept_whatever_its_age(box):
    path, state = box
    a = _bak(path, OLD, ["kid-gone"])
    b = _bak(path, "20261002T000000Z", ["kid-gone"])
    _prune(path, state)
    assert not a.exists() and b.exists()


def test_recent_and_unparseable_backups_are_kept(box):
    path, state = box
    recent = _bak(path, "20261120T000000Z", ["kid-gone"])
    broken = _bak(path, OLD, None, raw="{not json")
    odd_name = path.with_name(f"{path.name}.bak-whenever")
    odd_name.write_text(json.dumps(_creds(["kid-gone"])))
    _bak(path, RECENT, ["kid-now"])
    out = _prune(path, state)
    assert recent.exists() and broken.exists() and odd_name.exists()
    assert out["kept_unreadable"] == 1 and out["deleted"] == 0


def test_a_deleted_backup_is_overwritten_with_zeros_first(box, monkeypatch):
    path, state = box
    b = _bak(path, OLD, ["kid-gone"])
    _bak(path, RECENT, ["kid-now"])
    size = b.stat().st_size
    seen = {}
    real_unlink = Path.unlink

    def unlink(self, *a, **k):
        if self == b:
            seen["bytes"] = self.read_bytes()
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", unlink)
    _prune(path, state)
    assert seen["bytes"] == b"\0" * size and not b.exists()


def test_logs_carry_counts_never_a_kid(box, caplog):
    path, state = box
    _bak(path, OLD, ["kid-gone"])
    _bak(path, RECENT, ["kid-now"])
    with caplog.at_level(logging.INFO, logger=ak.__name__):
        _prune(path, state)
    assert "deleted 1" in caplog.text and "kid-" not in caplog.text and "PEM" not in caplog.text


def test_ensure_registered_runs_the_prune(box, monkeypatch):
    path, state = box
    calls = []
    monkeypatch.setattr(ak, "_ensure", lambda t, p: {"status": "already_registered"})
    monkeypatch.setattr(ak, "prune_key_backups", lambda **k: calls.append("keys"))
    monkeypatch.setattr(ak, "prune_env_backups", lambda *a, **k: calls.append("env"))
    assert ak.ensure_registered(path=path)["status"] == "already_registered"
    assert calls == ["keys", "env"]


def test_a_prune_error_never_breaks_ensure(box, monkeypatch):
    path, state = box
    monkeypatch.setattr(ak, "_ensure", lambda t, p: {"status": "already_registered"})
    monkeypatch.setattr(ak, "load_credentials", lambda p: (_ for _ in ()).throw(OSError("disk")))
    assert ak.prune_key_backups(path=path, now=NOW)["deleted"] == 0


# ── env-file copies ────────────────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_env_backups_are_0600_and_old_ones_go_newest_of_each_kind_kept(tmp_path):
    env = tmp_path / "agent.env"
    env.write_text("ETERNITAS_PASSPORT_TOKEN=x\n")
    made = {}
    for kind in ("ept", "deregister"):
        for stamp in (OLD, "20261002T000000Z", RECENT):
            b = tmp_path / f"agent.env.bak-{kind}-{stamp}"
            b.write_text("SECRET=1\n")
            os.chmod(b, 0o644)
            made[(kind, stamp)] = b
    only_old = tmp_path / "agent.env.bak-deregister-20260901T000000Z"
    out = ak.prune_env_backups(env, now=NOW)
    for kind in ("ept", "deregister"):
        assert not made[(kind, OLD)].exists() and not made[(kind, "20261002T000000Z")].exists()
        assert made[(kind, RECENT)].exists() and (made[(kind, RECENT)].stat().st_mode & 0o777) == 0o600
    assert out["deleted"] == 4 and not only_old.exists()


def test_a_single_old_env_backup_is_kept_as_the_newest(tmp_path):
    env = tmp_path / "agent.env"
    env.write_text("x\n")
    b = tmp_path / f"agent.env.bak-ept-{OLD}"
    b.write_text("SECRET=1\n")
    ak.prune_env_backups(env, now=NOW)
    assert b.exists()


def test_credentials_prune_never_touches_env_copies(box):
    path, state = box
    env_copy = path.with_name(f"agent.env.bak-ept-{OLD}")
    env_copy.write_text("SECRET=1\n")
    _bak(path, RECENT, ["kid-now"])
    _prune(path, state)
    assert env_copy.exists()
