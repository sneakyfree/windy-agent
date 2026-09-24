"""`windy bring-home`: the hub handover (AGENT_HANDOVER.md §9) against a fake
hub + fake Eternitas — key first, one-time pickup, sealed install, memory,
EPT by key proof, rollback, rotation, and no token ever printed."""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import logging
import os
import re
import stat
import uuid

import httpx
import pytest
from rich.console import Console

from windyfly import bring_home, hub_hatch
from windyfly.eternitas import agent_keys as ak

PASSPORT = "ET26-HOME-0001"
OWNER = "FRESH-OWNER-TOKEN-SECRET"
MATRIX_TOKEN = "syt_MATRIX_SECRET"
MAIL_PASS = "MAIL_APP_SECRET"
def _jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'ES256'})}.{enc(claims)}.EPT_SECRET_SIG"


EPT = _jwt({"sub": "ET26-HOME-0001", "exp": 4_000_000_000, "cvr": 1})
AGENT_MX = f"@agent_{PASSPORT.lower()}:chat.windychat.ai"
# Windy Chat's ndjson-v1 as seen live 2026-09-24: a header, then Matrix events.
NDJSON = (json.dumps({"type": "windy.memory.header", "format": "ndjson-v1", "event_count": 3,
                      "passport_number": PASSPORT, "matrix_user_id": AGENT_MX, "rooms": ["!dm:chat"]}).encode() + b"\n"
          + json.dumps({"type": "m.room.message", "sender": "@nora:chat.windychat.ai", "room_id": "!dm:chat",
                        "event_id": "$1", "origin_server_ts": 1, "content": {"msgtype": "m.text", "body": "hi pip"}}).encode() + b"\n"
          + json.dumps({"type": "m.room.message", "sender": AGENT_MX, "room_id": "!dm:chat",
                        "event_id": "$2", "origin_server_ts": 2, "content": {"msgtype": "m.text", "body": "hello Nora"}}).encode() + b"\n"
          + json.dumps({"type": "m.reaction", "sender": "@nora:chat.windychat.ai", "event_id": "$3",
                        "content": {"m.relates_to": {"key": "+1"}}}).encode() + b"\n")
LETTER = "Dear me: Nora likes short answers."


def _memory_doc(ndjson: bytes = NDJSON, *, fmt: str = "ndjson-v1", sha: str | None = None) -> dict:
    return {"handover_id": "ho_1", "passport_number": PASSPORT, "format": fmt,
            "inline": base64.b64encode(gzip.compress(ndjson)).decode(),
            "sha256": sha or hashlib.sha256(ndjson).hexdigest(),
            "event_count": len(bring_home.split_memory(ndjson)[1])}


class Fake:
    """hub.test (handover + pickup + memory) and eternitas.test (keys + EPT)."""

    def __init__(self, *, start=(202, None), pickups=None, memory=None, reauth_first=False):
        self.start = start
        self.pickups = list(pickups if pickups is not None else [
            (428, {"error": "authorization_pending", "waiting_for": ["chat"]}),
            (200, None),
        ])
        self.memory = memory if memory is not None else (200, _memory_doc())
        self.reauth_first = reauth_first
        self.keys: dict[str, str] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.idem: list[str] = []
        self.nonces: set[str] = set()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def pickup_body(self) -> dict:
        return {"handover_id": "ho_1", "kind": "handover", "passport_number": PASSPORT,
                "matrix": {"user_id": f"@agent_{PASSPORT.lower()}:chat.windychat.ai",
                           "access_token": MATRIX_TOKEN, "device_id": "handover-ho_1",
                           "homeserver": "https://chat.windychat.ai"},
                "mail": {"address": "pip@windymail.ai", "username": "pip@windymail.ai",
                         "imap": {"host": "mail.windymail.ai", "port": 993, "security": "ssl"},
                         "app_password": MAIL_PASS, "app_password_id": "ap_1"},
                "memory": {"format": "ndjson-v1", "sha256": "x", "event_count": 3,
                           "turnover_letter": LETTER,
                           "url": "https://hub.test/api/v1/agent/handover/ho_1/memory",
                           "expires_at": "2099-01-01T00:00:00Z"},
                "eternitas": {"passport_number": PASSPORT, "path": "owner",
                              "register_key_url": f"https://eternitas.test/api/v1/bots/{PASSPORT}/keys"}}

    def handle(self, req: httpx.Request) -> httpx.Response:
        bearer = req.headers.get("authorization", "").removeprefix("Bearer ")
        path = req.url.path
        self.calls.append((req.method, f"{req.url.host}{path}", bearer))
        if req.url.host == "hub.test":
            if path == f"/api/v1/agent/{PASSPORT}/handover":
                assert bearer == OWNER
                self.idem.append(req.headers.get("idempotency-key", ""))
                body = json.loads(req.content)
                assert body["to"] == "external" and 0 < len(body["runtime_label"]) <= 60
                if self.reauth_first:
                    self.reauth_first = False
                    return httpx.Response(401, json={"error": "reauth_required"})
                status, extra = self.start
                if status == 202:
                    return httpx.Response(202, json={
                        "handover_id": "ho_1", "status": "pending",
                        "poll_url": "https://hub.test/api/v1/agent/handover/ho_1",
                        "credentials_pickup": {"url": "https://hub.test/api/v1/agent/handover/ho_1/credentials",
                                               "expires_at": "2099-01-01T00:00:00Z"}})
                return httpx.Response(status, json=extra or {})
            if path == "/api/v1/agent/handover/ho_1/credentials":
                status, body = self.pickups.pop(0) if self.pickups else (410, {"error": "gone"})
                return httpx.Response(status, json=body if body is not None else self.pickup_body())
            if path == "/api/v1/agent/handover/ho_1/memory":
                status, doc = self.memory
                self.memory = (410, {"error": "gone"})
                return httpx.Response(status, json=doc)
        if req.url.host == "eternitas.test":
            if path == f"/api/v1/bots/{PASSPORT}/keys/challenge":
                n = uuid.uuid4().hex
                self.nonces.add(n)
                return httpx.Response(200, json={"passport": PASSPORT, "nonce": n})
            if path == f"/api/v1/bots/{PASSPORT}/keys" and req.method == "POST":
                body = json.loads(req.content)
                assert bearer == OWNER and body["reason"] == "handover" and body["custody"] == "agent"
                kid = ak.thumbprint(body["jwk"])
                self.keys[kid] = "active"
                return httpx.Response(201, json={"kid": kid, "registered_via": "owner_handover"})
            m = re.fullmatch(rf"/api/v1/bots/{PASSPORT}/keys/([^/]+)/revoke", path)
            if m:
                self.keys[m.group(1)] = "revoked"
                return httpx.Response(200, json={"kid": m.group(1), "status": "revoked"})
            if path == f"/api/v1/bots/{PASSPORT}/keys" and req.method == "GET":
                return httpx.Response(200, json={"keys": []})
            if path == f"/api/v1/bots/{PASSPORT}/ept/refresh":
                assert req.headers.get("eternitas-agent-proof"), "key proof, not a bearer"
                return httpx.Response(200, json={"ept_token": EPT, "reissued": True})
        return httpx.Response(404, json={"detail": "Not Found"})


@pytest.fixture(autouse=True)
def _restore_environ():
    # bring-home writes what it installs into os.environ (the runtime reads it
    # from there); never let that leak into other test modules.
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, _restore_environ):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WINDY_HUB_URL", "https://hub.test")
    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")
    monkeypatch.setenv("WINDY_ENV_FILE", str(tmp_path / "agent.env"))
    monkeypatch.setenv("WINDY_CREDENTIALS_FILE", str(tmp_path / "state" / "credentials.json"))
    monkeypatch.setenv("WINDYFLY_DB_PATH", str(tmp_path / "windyfly.db"))
    for var in ("ETERNITAS_PASSPORT", "ETERNITAS_PASSPORT_TOKEN", "MATRIX_BOT_TOKEN",
                "MATRIX_DEVICE_ID", "MATRIX_HOMESERVER", "MATRIX_BOT_USER", "MATRIX_DM_ROOM_ID",
                "WINDYMAIL_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "agent.env").write_text("KEEP_ME=1\n", encoding="utf-8")
    hub_hatch.remember_cloud_agent({"passport_number": PASSPORT, "agent": {"name": "Pip"},
                                    "platforms": {"chat": {"matrix_user_id": "@agent_x:chat",
                                                           "dm_room_id": "!dm:chat",
                                                           "url": "https://app.windychat.ai/?agent_room=x"}}})
    yield


def _run(fake: Fake, **kw):
    buf = io.StringIO()
    console = Console(file=buf, width=200, color_system=None)
    rc = bring_home.run(console, owner_token=kw.pop("owner_token", lambda: OWNER),
                        transport=fake.transport(), sleep=lambda s: None, **kw)
    return rc, buf.getvalue()


def _env(tmp_path) -> dict[str, str]:
    out = {}
    for line in (tmp_path / "agent.env").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def _episodes(tmp_path):
    from windyfly.memory.database import Database

    db = Database(str(tmp_path / "windyfly.db"))
    rows = db.fetchall("SELECT role, content, session_id FROM episodes ORDER BY created_at")
    letters = db.fetchall("SELECT metadata FROM nodes WHERE type = 'turnover_letter'")
    return rows, letters


def test_happy_path_brings_the_agent_home(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    fake = Fake()
    rc, out = _run(fake)
    assert rc == 0, out
    assert "Pip is home" in out and "windy start --channel matrix" in out and str(tmp_path) in out

    # key registered on the owner path BEFORE the handover started
    hosts = [c[1] for c in fake.calls]
    assert hosts.index(f"eternitas.test/api/v1/bots/{PASSPORT}/keys") < hosts.index(
        f"hub.test/api/v1/agent/{PASSPORT}/handover")
    assert list(fake.keys.values()) == ["active"]

    env = _env(tmp_path)
    assert env["KEEP_ME"] == "1"
    assert env["ETERNITAS_PASSPORT"] == PASSPORT
    assert env["MATRIX_BOT_TOKEN"] == MATRIX_TOKEN and env["MATRIX_DEVICE_ID"] == "handover-ho_1"
    assert env["MATRIX_HOMESERVER"] == "https://chat.windychat.ai"
    assert env["MATRIX_DM_ROOM_ID"] == "!dm:chat" and env["WINDYMAIL_EMAIL"] == "pip@windymail.ai"
    assert env["ETERNITAS_PASSPORT_TOKEN"] == EPT  # from the key proof, not the hub
    assert stat.S_IMODE(os.stat(tmp_path / "agent.env").st_mode) == 0o600

    creds = json.loads((tmp_path / "state" / "credentials.json").read_text())
    assert creds["windy_mail"]["app_password"] == MAIL_PASS
    assert creds["eternitas"]["kid"] in fake.keys

    rows, letters = _episodes(tmp_path)
    assert [(r["role"], r["content"]) for r in rows] == [("user", "hi pip"), ("assistant", "hello Nora")]
    assert all(r["session_id"] == "handover:ho_1" for r in rows)
    assert LETTER in letters[0]["metadata"]
    assert "1 kept only in the raw archive" in out
    assert (tmp_path / "state" / "memory-ho_1.ndjson").read_bytes() == NDJSON

    assert hub_hatch.cloud_agent()["where"] == "home"
    assert not (tmp_path / "state" / f"handover-{PASSPORT}.json").exists()  # sealed copy shredded
    for secret in (OWNER, MATRIX_TOKEN, MAIL_PASS, EPT):
        assert secret not in out and secret not in caplog.text


def test_second_run_and_windy_go_say_it_lives_here(tmp_path):
    _run(Fake())
    fake = Fake()
    rc, out = _run(fake)
    assert rc == 0 and "already lives on this machine" in out and fake.calls == []
    buf = io.StringIO()
    assert hub_hatch.go(Console(file=buf, width=200, color_system=None)) == 0
    assert "lives on this machine" in buf.getvalue()


def test_reauth_required_signs_in_once_more():
    logins = []
    fake = Fake(reauth_first=True)
    rc, _ = _run(fake, owner_token=lambda: logins.append(1) or OWNER)
    assert rc == 0 and len(logins) == 2
    assert fake.idem[0] == fake.idem[1]  # same round, same key


@pytest.mark.parametrize("status, extra, words", [
    (409, {"error": "already_external"}, "already lives outside the cloud"),
    (503, {"error": "handover_unavailable"}, "can't hand agents over right now"),
    (500, {"error": "boom"}, "refused the handover"),
])
def test_a_refused_handover_revokes_the_new_key_and_changes_nothing(tmp_path, status, extra, words):
    fake = Fake(start=(status, extra))
    rc, out = _run(fake)
    assert rc == 1 and words in out
    assert list(fake.keys.values()) == ["revoked"]
    assert hub_hatch.cloud_agent()["where"] == "cloud"
    assert "MATRIX_BOT_TOKEN" not in _env(tmp_path)


def test_a_gone_pickup_says_run_again(tmp_path):
    fake = Fake(pickups=[(410, {"error": "gone"})])
    rc, out = _run(fake)
    assert rc == 1 and "run" in out.lower() and "windy bring-home" in out
    assert hub_hatch.cloud_agent()["where"] == "cloud"


def test_idempotency_key_is_stable_so_a_rerun_is_a_rotate_round():
    a, b = Fake(pickups=[(410, {})]), Fake(pickups=[(410, {})])
    _run(a)
    _run(b)
    assert a.idem and a.idem == b.idem
    assert a.idem[0].startswith("windyfly-home-") and a.idem[0].endswith(PASSPORT)


def test_a_crash_after_the_pickup_resumes_from_the_sealed_copy(tmp_path, monkeypatch):
    real = bring_home.install_credentials
    calls = {"n": 0}

    def crash_once(body, rec):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("power cut")
        return real(body, rec)

    monkeypatch.setattr(bring_home, "install_credentials", crash_once)
    with pytest.raises(RuntimeError):
        _run(Fake())
    sealed = tmp_path / "state" / f"handover-{PASSPORT}.json"
    assert sealed.exists() and stat.S_IMODE(os.stat(sealed).st_mode) == 0o600

    fake = Fake()  # the pickup is gone at the hub; the sealed copy is enough
    rc, out = _run(fake)
    assert rc == 0 and "Resuming" in out
    assert not any("handover" in c[1] and "credentials" not in c[1] and c[1].endswith("/handover")
                   for c in fake.calls)
    assert _env(tmp_path)["MATRIX_BOT_TOKEN"] == MATRIX_TOKEN
    assert not sealed.exists()


def test_unknown_memory_format_is_kept_not_guessed(tmp_path):
    fake = Fake(memory=(200, {**_memory_doc(), "format": "jsonl-v9"}))
    rc, out = _run(fake)
    assert rc == 0 and "wasn't imported (unknown_format)" in out
    rows, letters = _episodes(tmp_path)
    assert rows == [] and LETTER in letters[0]["metadata"]
    assert (tmp_path / "state" / "memory-ho_1.raw.json").exists()


def test_memory_checksum_mismatch_is_not_imported(tmp_path):
    fake = Fake(memory=(200, _memory_doc(sha="0" * 64)))
    rc, out = _run(fake)
    assert rc == 0 and "sha_mismatch" in out
    assert _episodes(tmp_path)[0] == []


def test_no_cloud_agent_says_run_windy_go(tmp_path):
    hub_hatch.cloud_agent_path().unlink()
    fake = Fake()
    rc, out = _run(fake)
    assert rc == 1 and "run windy go" in out.replace("[bold]", "").replace("[/bold]", "").lower() \
        or "windy go" in out
    assert fake.calls == []


def test_generic_role_lines_still_import():
    from windyfly.memory.database import Database

    db = Database(":memory:")
    out = bring_home.import_memory(b'{"role":"user","content":"yo"}\n{"role":"assistant","text":"hey"}\n',
                                   handover_id="h", db=db)
    assert out == {"imported": 2, "skipped": 0}


def test_count_mismatch_is_not_imported(tmp_path):
    fake = Fake(memory=(200, {**_memory_doc(), "event_count": 7}))
    rc, out = _run(fake)
    assert rc == 0 and "count_mismatch" in out
    assert _episodes(tmp_path)[0] == []


def test_upsert_env_is_atomic_and_keeps_other_lines(tmp_path):
    p = tmp_path / "x.env"
    p.write_text("A=1\n# comment\nB=2\n", encoding="utf-8")
    bring_home.upsert_env({"B": "3", "C": "4"}, p)
    assert p.read_text(encoding="utf-8") == "A=1\n# comment\nB=3\nC=4\n"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".x.env.")]  # no temp left behind
