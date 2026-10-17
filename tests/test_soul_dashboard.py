"""Soul import from the dashboard, the brain's side (strand P2.21.13, soul-import PR 4; Hub's conditions)."""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from windyfly.memory.database import Database
from windyfly.soul_import import flow
from windyfly.soul_import import intake as ik

CANARY = "zqxdash5151"


def _export() -> bytes:
    return json.dumps([{"title": f"Trip {CANARY}", "mapping": {"a": {"message": {
        "author": {"role": "user"}, "content": {"parts": [f"I prefer aisle seats {CANARY}"]}}}}}]).encode()


@pytest.fixture
def run_dir(monkeypatch, tmp_path):
    d = tmp_path / "run"
    d.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(d))
    monkeypatch.setattr(ik, "upload_dirs", lambda: [d.resolve()])
    intake_dir = tmp_path / "intake"
    intake_dir.mkdir()
    monkeypatch.setattr(ik, "_base_dir", lambda: intake_dir)
    for held in (flow._pending, flow._confirm, flow._tones):
        held.clear()
    yield d
    for held in (flow._pending, flow._confirm, flow._tones):
        held.clear()


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _upload(run_dir, data: bytes = b"", name: str | None = None):
    p = run_dir / (name or ("windy-soul-upload-" + os.urandom(16).hex()))
    p.write_bytes(data or _export())
    os.chmod(p, 0o600)
    return p


def test_an_upload_is_previewed_by_token_and_the_file_is_gone(run_dir, db):
    f = _upload(run_dir)
    out = flow.dashboard_upload(db, str(f), "json")
    assert "Soul Preview" in out["preview"] and len(out["token"]) == 32
    assert not f.exists()
    assert flow.dashboard_preview(out["token"]) == {"preview": out["preview"]}
    assert db.fetchone("SELECT COUNT(*) AS n FROM nodes")["n"] == 0        # nothing kept before approve
    done = flow.dashboard_approve(db, out["token"])
    assert done["imported"] >= 1
    assert "error" in flow.dashboard_approve(db, out["token"])            # single use


def test_reject_keeps_nothing(run_dir, db):
    out = flow.dashboard_upload(db, str(_upload(run_dir)), "json")
    assert flow.dashboard_reject(out["token"]) == {"rejected": True}
    assert "error" in flow.dashboard_preview(out["token"])
    assert db.fetchone("SELECT COUNT(*) AS n FROM nodes")["n"] == 0
    assert not list((run_dir.parent / "intake").iterdir())


def test_only_the_gateways_own_upload_file_is_taken(run_dir, db, tmp_path):
    elsewhere = tmp_path / ("windy-soul-upload-" + "c" * 32)
    elsewhere.write_bytes(_export())
    assert "error" in flow.dashboard_upload(db, str(elsewhere), "json")    # right name, wrong folder
    assert elsewhere.read_bytes() == _export()                             # and left alone
    wrong_name = _upload(run_dir, name="conversations.json")
    assert "error" in flow.dashboard_upload(db, str(wrong_name), "json")
    assert wrong_name.exists()
    traversal = str(run_dir / ".." / "run" / ("windy-soul-upload-" + "d" * 32))
    assert "error" in flow.dashboard_upload(db, traversal, "json")         # no such file: refused
    assert "error" in flow.dashboard_upload(db, "/etc/passwd", "json")


def test_a_link_swapped_in_for_the_upload_is_never_followed(run_dir, db, tmp_path):
    secret = tmp_path / "owners-file.json"
    secret.write_bytes(_export())
    link = run_dir / ("windy-soul-upload-" + "e" * 32)
    link.symlink_to(secret)
    out = flow.dashboard_upload(db, str(link), "json")
    assert "not a plain file" in out["error"]
    assert secret.read_bytes() == _export()                                # never read, never wiped
    assert db.fetchone("SELECT COUNT(*) AS n FROM nodes")["n"] == 0


def test_one_dashboard_import_waits_at_a_time(run_dir, db):
    first = flow.dashboard_upload(db, str(_upload(run_dir)), "json")
    second_file = _upload(run_dir)
    second = flow.dashboard_upload(db, str(second_file), "json")
    assert "Another import is waiting" in second["error"]
    assert not second_file.exists()                                        # the refused upload is wiped too
    flow.dashboard_reject(first["token"])
    assert "token" in flow.dashboard_upload(db, str(_upload(run_dir)), "json")


def test_guest_mode_refuses_the_dashboard_upload(run_dir, db, monkeypatch, tmp_path):
    flag = tmp_path / ".guest"
    flag.write_text("{}")
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(flag))
    f = _upload(run_dir)
    assert "guest mode" in flow.dashboard_upload(db, str(f), "json")["error"]
    assert not f.exists()


def test_tokens_are_checked_before_use(run_dir, db):
    # even a key that exists is not reachable with a short token (a guessable one never matches)
    flow._pending["dashboard:short"] = flow._Pending(intake=ik.Intake("x", run_dir), preview="p")
    assert "error" in flow.dashboard_preview("short")
    flow._pending.clear()
    for bad in (None, 5, "", "x" * 10, "x" * 65, ["t"]):
        assert "error" in flow.dashboard_preview(bad)
        assert "error" in flow.dashboard_approve(db, bad)
        assert flow.dashboard_reject(bad) == {"rejected": False}


def test_the_bridge_no_longer_reads_a_host_path(run_dir, db, tmp_path):
    from windyfly.bridge.uds_server import UDSBridge

    folder = tmp_path / "export"
    folder.mkdir()
    (folder / "conversations.json").write_bytes(_export())
    server = UDSBridge.__new__(UDSBridge)
    server.db = db
    for handler in (server._handle_soul_preview, server._handle_soul_import):
        out = asyncio.run(handler({"export_path": str(folder)}))
        assert "error" in out and "preview" not in out
    assert db.fetchone("SELECT COUNT(*) AS n FROM nodes")["n"] == 0
    assert (folder / "conversations.json").exists()


def test_a_tone_approved_on_the_dashboard_can_be_applied_from_chat(run_dir, db):
    flow._tones[flow.DASHBOARD_TONE] = (time.time(), {"humor": 9}, "chatgpt")
    assert "humor: 5 -> 9" in flow.apply(db, "matrix:!dm:example.test")


def test_old_uploads_are_swept_and_links_removed_without_following(run_dir, tmp_path):
    old = time.time() - ik.MAX_AGE_S - 5
    stale = _upload(run_dir)
    os.utime(stale, (old, old))
    fresh = _upload(run_dir)
    target = tmp_path / "keep.txt"
    target.write_text("keep")
    link = run_dir / ("windy-soul-upload-" + "f" * 32)
    link.symlink_to(target)
    os.utime(link, (old, old), follow_symlinks=False)
    assert ik.sweep_uploads(dirs=[run_dir]) == 2
    assert not stale.exists() and fresh.exists() and not link.is_symlink()
    assert target.read_text() == "keep"


def test_a_dashboard_approve_then_soul_apply_in_chat_end_to_end(run_dir, db):
    """Hub on #588: the tone from a dashboard import must be reachable from the owner's chat."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SOUL.md", "- Warm\n")
        z.writestr("config.yaml", "humor: 8\nformality: 2\n")
    up = flow.dashboard_upload(db, str(_upload(run_dir, buf.getvalue())), "zip")
    done = flow.dashboard_approve(db, up["token"])
    assert done["tone"] == {"humor": 8, "formality": 2}
    asked = flow.apply(db, "matrix:!dm:example.test")
    assert "humor: 5 -> 8" in asked and "formality: 5 -> 2" in asked and "from your openclaw export" in asked
    assert flow.confirm(db, "matrix:!dm:example.test").startswith("Applied the imported tone")


def test_a_chatgpt_tone_is_called_an_estimate(run_dir, db):
    flow.offer_tone("cli:x", {"humor": 9}, "chatgpt")
    assert "estimated from how you write in your chatgpt export" in flow.apply(db, "cli:x")

