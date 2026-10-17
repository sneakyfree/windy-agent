"""Strand v2, new genes N4-N8 (Mail, Calendar, Cloud, phone tools, Vault): the promises no other test
proved (docs/strand/STRAND_V2.md, Wave 5g2). Synthetic addresses, fake keys, no network."""

from __future__ import annotations

import ast
import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from windyfly import cloud_backup as cb
from windyfly.agent import service_auth
from windyfly.agent import windy_calendar as wc
from windyfly.agent.capabilities.descriptor import Band
from windyfly.channels import phone_tools
from windyfly.tools import mail

ROOT = Path(__file__).resolve().parents[2]


# ── N4 Windy Mail ────────────────────────────────────────────────────────────────────────

def test_n4_an_address_carrying_a_real_newline_header_is_refused():
    with pytest.raises(ValueError, match="doesn't look like an email address"):
        mail.normalize_recipients("probe@example.invalid\nBcc: x@example.invalid")


def test_n4_a_trust_gate_denial_sends_nothing(monkeypatch):
    from windyfly.channels.email import WindyMailAdapter
    from windyfly.trust.gate import TrustDenied

    monkeypatch.setenv("WINDYMAIL_EMAIL", "agent@example.invalid")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "fake-ept")

    def deny(*a, **k):
        raise TrustDenied("send_email", "critical", "the band is too low")

    monkeypatch.setattr("windyfly.trust.gate.require_trust_sync", deny)
    monkeypatch.setattr("httpx.post", lambda *a, **k: pytest.fail("nothing is sent when the gate denies"))
    out = WindyMailAdapter().send_email("probe@example.invalid", "hi", "body")
    assert out["status"] == "denied"


def test_n4_a_held_draft_expires_after_30_minutes(monkeypatch):
    mail._PENDING.clear()
    try:
        assert mail._queue_draft("probe@example.invalid", "hi", "body")["status"] == "pending_owner_approval"
        assert len(mail.pending_drafts()) == 1
        later = time.time() + mail.PENDING_TTL_S + 1
        monkeypatch.setattr(time, "time", lambda: later)
        assert mail.pending_drafts() == []
    finally:
        mail._PENDING.clear()


# ── N5 Windy Calendar ────────────────────────────────────────────────────────────────────

def test_n5_a_network_error_is_said_plainly(monkeypatch):
    monkeypatch.setenv(wc.ENV_FLAG, "1")
    monkeypatch.setattr(service_auth, "agent_headers", lambda aud, m, u: {"Authorization": "Bearer t", "DPoP": "p"})

    def down(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("httpx.post", down)
    out = wc.invoke("get_booking_page")
    assert out == {"ok": False, "say": "I couldn't reach Windy Calendar just now. Try again in a moment."}


# ── N6 Windy Cloud: restore safety and the backup schedule ───────────────────────────────

@pytest.fixture
def cloud(monkeypatch, tmp_path):
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-0001")
    monkeypatch.delenv("WINDY_BACKUP_KEY", raising=False)
    monkeypatch.setattr(cb, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cb, "_BACKUP_STATE_FILE", tmp_path / "data" / "backup_state.json")
    return tmp_path


def test_n6_a_restore_keeps_the_current_database_as_pre_restore(cloud, monkeypatch):
    key = cb._get_encryption_key()
    stored = cb._encrypt_data(b"SQLite format 3\x00 the backed-up memory", key)
    db = cloud / "data" / "windyfly.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"SQLite format 3\x00 today's memory")
    monkeypatch.setattr("windyfly.auth.bot_credentials.ecosystem_auth_header",
                        AsyncMock(return_value={"Authorization": "Bearer t"}))
    monkeypatch.setattr("windyfly.auth.bot_credentials.get_bot_key", AsyncMock(return_value=None))

    async def get(url, headers=None):
        r = MagicMock(status_code=200)
        if "/list/" in url:
            r.json.return_value = {"files": [{"filename": "windyfly-x.enc", "size_bytes": len(stored),
                                              "created_at": "2026-10-01T00:00:00Z"}], "count": 1}
        else:
            r.content = stored
        r.raise_for_status = MagicMock()
        return r

    client = AsyncMock()
    client.get = AsyncMock(side_effect=get)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: ctx)
    res = asyncio.run(cb.restore_from_cloud("latest"))
    assert res["success"] is True, res
    assert db.read_bytes() == b"SQLite format 3\x00 the backed-up memory"
    assert db.with_suffix(".db.pre-restore").read_bytes() == b"SQLite format 3\x00 today's memory"


def _last_backup(cloud, hours_ago: float) -> None:
    when = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    cb._BACKUP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cb._BACKUP_STATE_FILE.write_text('{"last_backup": "%s"}' % when.isoformat(), encoding="utf-8")


def test_n6_a_backup_is_due_after_24_hours_by_default(cloud):
    _last_backup(cloud, 23)
    assert cb.should_backup({}) is False
    _last_backup(cloud, 25)
    assert cb.should_backup({}) is True


def test_n6_auto_backup_off_means_never_due(cloud):
    _last_backup(cloud, 100)
    assert cb.should_backup({"cloud": {"auto_backup": False}}) is False


def test_n6_no_cloud_credential_means_no_scheduled_backup(cloud, monkeypatch):
    monkeypatch.delenv("WINDY_CLOUD_TOKEN", raising=False)
    monkeypatch.delenv("WINDY_JWT", raising=False)
    monkeypatch.setattr(cb, "backup_to_cloud", AsyncMock(side_effect=AssertionError("no backup without a token")))
    assert asyncio.run(cb.run_backup_if_due({})) is None


# ── N7 phone tools ───────────────────────────────────────────────────────────────────────

def test_n7_only_an_owner_turn_is_offered_the_phone(monkeypatch):
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot

    picked: list[str] = []
    marked: dict[str, object] = {}
    monkeypatch.setattr(phone_tools, "pick_phone", lambda room, sender: picked.append(sender) or "phone-1")
    monkeypatch.setattr(phone_tools, "mark_turn", lambda sid, target: marked.__setitem__(sid, target))
    for sid, band in (("s-trusted", Band.TRUSTED), ("s-user", Band.USER), ("s-owner", Band.OWNER)):
        asyncio.run(WindyFlyMatrixBot._mark_phone(None, sid, "!dm:x", "@owner:x", band))
    assert picked == ["@owner:x"]
    assert marked == {"s-trusted": None, "s-user": None, "s-owner": "phone-1"}


def test_n7_the_phone_tools_are_not_registered_when_off(monkeypatch):
    from windyfly.agent.capabilities.phone import register_phone_capabilities

    monkeypatch.delenv("WINDY_PHONE_TOOLS", raising=False)
    registry = MagicMock()
    register_phone_capabilities(registry)
    registry.register.assert_not_called()


# ── N8 Windy Vault ───────────────────────────────────────────────────────────────────────

def test_n8_nothing_outside_the_vault_package_imports_it():
    src = ROOT / "src" / "windyfly"
    users = []
    for py in src.rglob("*.py"):
        if py.is_relative_to(src / "vault"):
            continue
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            if isinstance(node, ast.ImportFrom) and node.module == "windyfly":
                names += [f"windyfly.{a.name}" for a in node.names]
            if any(n == "windyfly.vault" or n.startswith("windyfly.vault.") for n in names):
                users.append(str(py.relative_to(src)))
    assert users == []



def test_n8_a_bad_lease_key_file_is_logged_not_replaced_silently(tmp_path, caplog):
    from windyfly.vault import lease_key

    path = tmp_path / "lease_keys.json"
    path.write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING", logger="windyfly.vault.lease_key"):
        entry = lease_key.ensure_active(path=path)
    msgs = [r.getMessage() for r in caplog.records if r.name == "windyfly.vault.lease_key"]
    assert len(msgs) == 1 and str(path) in msgs[0] and "replaces it" in msgs[0]
    assert "{not json" not in msgs[0]
    assert lease_key._load(path)["keys"][0]["kid"] == entry["kid"]


def test_n6_no_backup_is_uploaded_without_an_owner_held_key(monkeypatch, caplog):
    """Boss 10-17: a key derived from passport + name is never used for an upload."""
    from unittest.mock import AsyncMock

    monkeypatch.delenv("WINDY_BACKUP_KEY", raising=False)
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-0001")
    trust = AsyncMock(return_value=None)
    auth = AsyncMock(return_value={"Authorization": "Bearer t"})
    monkeypatch.setattr("windyfly.trust.gate.require_trust", trust)
    monkeypatch.setattr("windyfly.auth.bot_credentials.ecosystem_auth_header", auth)
    before = cb._derived_key_refusals
    with caplog.at_level("WARNING", logger="windyfly.cloud_backup"):
        out = asyncio.run(cb.backup_to_cloud())
    assert out["success"] is False and "no backup key of its own" in out["error"]
    assert cb._derived_key_refusals == before + 1
    trust.assert_not_awaited()                                       # nothing was prepared or sent
    auth.assert_not_awaited()
    assert any("backup not sent" in r.getMessage() for r in caplog.records)
    assert cb.owner_key_missing() is True
    monkeypatch.setenv("WINDY_BACKUP_KEY", "owner-secret")
    assert cb.owner_key_missing() is False


def test_n4_the_inbox_watch_runs_in_exactly_one_process_and_matrix_can_run_it(monkeypatch):
    """Strand N4.30: one maintenance runner per agent; when it is Matrix, new mail reaches the owner's room."""
    import threading

    from tests.test_matrix_bot import _make_config
    from windyfly.agent import maintenance
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue

    monkeypatch.delenv("WINDY_MAINTENANCE_RUNNER", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    assert maintenance.maintenance_runner() == "telegram"          # Zero's case: both units agree
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert maintenance.maintenance_runner() == "matrix"            # a Matrix-only agent
    monkeypatch.setenv("WINDY_MAINTENANCE_RUNNER", "telegram")
    assert maintenance.maintenance_runner() == "telegram"          # pinned

    bot = WindyFlyMatrixBot(_make_config(), Database(":memory:"), WriteQueue())
    bot._hatch_dm_room_id = "!owner-dm:chat.example.test"
    bot.client.room_send = AsyncMock()
    seen = {}

    def fake_make_job(notify, agent_name=""):
        seen["notify"] = notify
        return "job"

    async def go():
        monkeypatch.setattr("windyfly.agent.inbox_watch.make_inbox_watch_job", fake_make_job)
        assert bot._make_inbox_watch_job() == "job"
        t = threading.Thread(target=seen["notify"], args=("📬 New mail from Sam",))
        t.start()
        while t.is_alive():
            await asyncio.sleep(0.01)

    asyncio.run(go())
    bot.client.room_send.assert_awaited_once()
    room, kind, content = bot.client.room_send.await_args.args
    assert room == "!owner-dm:chat.example.test" and content["body"] == "📬 New mail from Sam"
