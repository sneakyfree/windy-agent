"""`windy home pair` (Windy Connect #105): a code the owner's phone approves brings the agent home to this computer.

The computer never sees the owner's sign-in: it registers its OWN key with the one-time bootstrap token, then takes the
handed-over memory once. Secrets (the device code, the bootstrap token, the memory link) never appear in any output."""

from __future__ import annotations

import io
import json
import logging
import os

import httpx
import pytest
from rich.console import Console

from tests.test_bring_home import _memory_doc
from windyfly import home_pair, hub_hatch
from windyfly.eternitas import agent_keys as ak

PASSPORT = "ET26-PAIR-0001"
BOOT = "BOOTSTRAP-TOKEN-SECRET"
DEVICE = "DEVICE-CODE-SECRET"
CLAIM = "MEMORY-CLAIM-SECRET"
SECRETS = (BOOT, DEVICE, CLAIM)


@pytest.fixture
def _restore_environ():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, _restore_environ):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WINDY_CONNECT_URL", "https://connect.test")
    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")
    monkeypatch.setenv("WINDY_ENV_FILE", str(tmp_path / "agent.env"))
    monkeypatch.setenv("WINDY_CREDENTIALS_FILE", str(tmp_path / "state" / "credentials.json"))
    monkeypatch.setenv("WINDYFLY_DB_PATH", str(tmp_path / "windyfly.db"))
    for var in ("ETERNITAS_PASSPORT", "ETERNITAS_PASSPORT_TOKEN", "WINDY_CHAT_EPT_AGENT", "WINDY_MAIL_EPT_AGENT"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "agent.env").write_text("KEEP_ME=1\n", encoding="utf-8")
    log = logging.getLogger("httpx")
    for f in [f for f in log.filters if isinstance(f, home_pair._QueryValuesHidden)]:
        log.removeFilter(f)  # each test proves the filter is installed by the round itself
    yield


class Fake:
    """connect.test (code, poll, cancel, the memory link) and eternitas.test (key challenge + registration)."""

    def __init__(self, *, polls=None, memory=None, keys_status=201):
        self.polls = list(polls if polls is not None else [(428, {"error": "authorization_pending"}), (200, None)])
        self.memory = memory if memory is not None else (200, _memory_doc())
        self.keys_status = keys_status
        self.calls: list[tuple[str, str, dict, dict]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def paths(self) -> list[str]:
        return [p for p, *_ in self.calls]

    def handle(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        path = req.url.path
        self.calls.append((path, req.method, body, dict(req.headers)))
        if path == "/v1/home/code":
            return httpx.Response(200, json={"device_code": DEVICE, "user_code": "1234-5678",
                                             "qr_url": "https://connect.test/home?c=12345678",
                                             "expires_in": 300, "interval": 5})
        if path == "/v1/home/poll":
            status, data = self.polls.pop(0)
            if status == 200 and data is None:
                data = {"passport_number": PASSPORT, "agent_name": "Pip", "bootstrap_token": BOOT,
                        "bootstrap_expires_at": "2026-11-11T12:00:00Z",
                        "memory": {"url": f"https://connect.test/v1/device/memory?claim={CLAIM}"}}
            return httpx.Response(status, json=data)
        if path == "/v1/home/cancel":
            return httpx.Response(200, json={"ok": True})
        if path == "/v1/device/memory":
            status, doc = self.memory
            return httpx.Response(status, json=doc)
        if path.endswith("/keys/challenge"):
            return httpx.Response(200, json={"nonce": "n-1"})
        if path == f"/api/v1/bots/{PASSPORT}/keys" and req.method == "POST":
            if self.keys_status != 201:
                return httpx.Response(self.keys_status, json={"detail": {"code": "bootstrap_invalid",
                                                                         "message": "no"}})
            return httpx.Response(201, json={"kid": ak.thumbprint(body["jwk"])})
        return httpx.Response(404)


def _pair(fake: Fake, *, json_status=False, **kw):
    buf, out = io.StringIO(), io.StringIO()
    console = Console(file=buf, width=200, color_system=None)
    t = {"now": 1000.0}

    def sleep(s):
        t["now"] += s

    rc = home_pair.pair(console, json_status=json_status, transport=fake.transport(), sleep=sleep,
                        now=lambda: t["now"], out=out, **kw)
    return rc, buf.getvalue(), out.getvalue()


def _env(tmp_path) -> dict[str, str]:
    lines = (tmp_path / "agent.env").read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


def _no_secret_anywhere(tmp_path, *texts: str) -> None:
    blobs = list(texts)
    for f in tmp_path.rglob("*"):
        if f.is_file() and f.suffix not in (".db", ".ndjson") and "-wal" not in f.name:
            blobs.append(f.read_text(encoding="utf-8", errors="replace"))
    for secret in SECRETS:
        assert not any(secret in b for b in blobs), secret


def test_a_paired_computer_registers_its_own_key_and_brings_the_agent_home(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    fake = Fake()
    rc, said, _ = _pair(fake)
    assert rc == 0, said
    assert "1234-5678" in said and "Pip now lives on this computer" in said
    kid = ak.local_kid()
    code_req = next(c for c in fake.calls if c[0] == "/v1/home/code")
    assert code_req[2]["agent_kid"] == kid and code_req[2]["client"] == "windyfly"
    assert all(c[2]["agent_kid"] == kid for c in fake.calls if c[0] == "/v1/home/poll")
    reg = next(c for c in fake.calls if c[0] == f"/api/v1/bots/{PASSPORT}/keys")
    assert reg[3]["authorization"] == f"Bearer {BOOT}"          # the bootstrap token, never an owner's
    assert ak.has_registered_key()
    env = _env(tmp_path)
    assert env["ETERNITAS_PASSPORT"] == PASSPORT and env["WINDY_CHAT_EPT_AGENT"] == "1" and env["KEEP_ME"] == "1"
    rec = hub_hatch.cloud_agent()
    assert rec["where"] == "home" and rec["door"] == "connect" and rec["passport_number"] == PASSPORT
    _no_secret_anywhere(tmp_path, said, caplog.text)


def test_the_key_is_registered_before_the_memory_is_taken(tmp_path):
    fake = Fake()
    assert _pair(fake)[0] == 0
    order = [p for p in fake.paths() if p in (f"/api/v1/bots/{PASSPORT}/keys", "/v1/device/memory")]
    assert order == [f"/api/v1/bots/{PASSPORT}/keys", "/v1/device/memory"]


def test_the_memory_link_is_fetched_once_with_no_credential(tmp_path):
    from windyfly.memory.database import Database

    fake = Fake()
    assert _pair(fake)[0] == 0
    gets = [c for c in fake.calls if c[0] == "/v1/device/memory"]
    assert len(gets) == 1 and "authorization" not in gets[0][3]
    db = Database(str(tmp_path / "windyfly.db"))
    assert db.fetchone("SELECT COUNT(*) AS c FROM episodes")["c"] > 0
    db.close()


def test_json_status_is_one_object_per_line_and_never_a_secret(tmp_path):
    rc, said, out = _pair(Fake(), json_status=True)
    assert rc == 0 and said == ""
    every = [json.loads(line) for line in out.splitlines()]
    events = [e for e in every if e["event"] != "progress"]
    assert [e["event"] for e in events] == ["code", "approved", "registered", "memory", "done"]
    assert events[0]["user_code"] == "1234-5678" and events[0]["qr_url"].startswith("https://connect.test/home")
    assert events[3]["status"] == "imported" and events[4]["passport"] == PASSPORT
    assert {e["stage"] for e in every if e["event"] == "progress"} >= {"waiting", "download", "import"}
    _no_secret_anywhere(tmp_path, out)


@pytest.mark.parametrize("status, code", [(403, "denied"), (410, "expired"), (502, "bootstrap_unavailable")])
def test_a_refused_round_changes_nothing(tmp_path, status, code):
    fake = Fake(polls=[(428, {}), (status, {"error": code})])
    rc, _, out = _pair(fake, json_status=True)
    assert rc == 1 and json.loads(out.splitlines()[-1]) == {"event": "error", "code": code}
    assert f"/api/v1/bots/{PASSPORT}/keys" not in fake.paths() and not ak.has_registered_key()
    assert hub_hatch.cloud_agent() == {} and "ETERNITAS_PASSPORT" not in _env(tmp_path)


def test_an_approval_that_takes_longer_than_the_code_s_300_s_still_completes(tmp_path):
    """Connect keeps an APPROVED code alive 900 s more and answers 428 meanwhile (Hub on #700)."""
    fake = Fake(polls=[(428, {})] * 100 + [(200, None)])      # 500 s of waiting
    assert _pair(fake)[0] == 0 and hub_hatch.cloud_agent()["where"] == "home"


def test_our_own_timer_is_only_a_safety_cap(tmp_path):
    fake = Fake(polls=[(428, {})] * 400)
    rc, said, _ = _pair(fake)
    assert rc == 1 and "expired" in said
    assert 60 * 4 < fake.paths().count("/v1/home/poll") <= (300 + home_pair.APPROVED_TTL_S + 60) // 5


def test_a_429_keeps_polling(tmp_path):
    fake = Fake(polls=[(429, {}), (428, {}), (200, None)])
    assert _pair(fake)[0] == 0


def test_a_refused_key_ends_the_round_before_the_memory(tmp_path):
    fake = Fake(keys_status=401)
    rc, _, out = _pair(fake, json_status=True)
    assert rc == 1 and json.loads(out.splitlines()[-1])["code"] == "key_registration_failed"
    assert "/v1/device/memory" not in fake.paths() and hub_hatch.cloud_agent() == {}


def test_a_spent_memory_link_starts_the_agent_without_it_and_says_so(tmp_path):
    fake = Fake(memory=(410, {"error": "gone"}))
    rc, said, _ = _pair(fake)
    assert rc == 0 and "Pip's memory couldn't be brought over, so it starts here without it" in said
    assert hub_hatch.cloud_agent()["where"] == "home"
    assert fake.paths().count("/v1/device/memory") == 1     # never retried: nothing can re-issue it


def test_an_agent_already_living_here_is_never_replaced(tmp_path):
    hub_hatch._write_private_json(hub_hatch.cloud_agent_path(), {"passport_number": "ET26-OLD", "where": "home"})
    fake = Fake()
    rc, _, out = _pair(fake, json_status=True)
    assert rc == 1 and json.loads(out) == {"event": "error", "code": "already_home", "passport": "ET26-OLD"}
    assert fake.calls == []


def _cred(tmp_path):
    return tmp_path / "state" / "credentials.json"


@pytest.mark.parametrize("polls", [[(403, {"error": "denied"})], [(410, {"error": "expired"})],
                                   [(502, {"error": "bootstrap_unavailable"})]])
def test_a_failed_round_leaves_no_key_behind_so_nothing_was_changed_is_true(tmp_path, polls):
    """Boss 11-03: "Nothing was changed" is only true if no key file or state stays behind."""
    assert _pair(Fake(polls=polls))[0] == 1
    state = tmp_path / "state"
    assert not state.exists() or not any(state.iterdir())   # no key file, no backup, nothing


def test_a_refused_key_leaves_no_key_behind(tmp_path):
    assert _pair(Fake(keys_status=401))[0] == 1
    assert not _cred(tmp_path).exists()


def test_a_credentials_file_that_was_there_before_comes_back_byte_for_byte(tmp_path):
    _cred(tmp_path).parent.mkdir(parents=True)
    _cred(tmp_path).write_text('{"other": {"keep": "me"}}\n', encoding="utf-8")
    before = _cred(tmp_path).read_bytes()
    assert _pair(Fake(polls=[(403, {"error": "denied"})]))[0] == 1
    assert _cred(tmp_path).read_bytes() == before
    assert sorted(p.name for p in _cred(tmp_path).parent.iterdir()) == ["credentials.json"]  # no backup left


def test_a_key_that_existed_before_is_never_removed(tmp_path):
    kid = ak.local_kid()
    assert _pair(Fake(polls=[(403, {"error": "denied"})]))[0] == 1
    assert ak.local_kid() == kid


def test_a_registered_key_stays(tmp_path):
    assert _pair(Fake())[0] == 0 and ak.has_registered_key()


def test_no_secret_reaches_any_log_line(tmp_path, caplog):
    """Boss 11-03: fails if any secret (the device code, the bootstrap token, the memory link's query) reaches a
    log line, from ANY logger, at ANY level."""
    caplog.set_level(logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    logging.getLogger("httpcore").setLevel(logging.DEBUG)
    assert _pair(Fake())[0] == 0
    lines = [r.getMessage() for r in caplog.records]
    assert any("v1/device/memory" in line for line in lines)        # the request WAS logged ...
    for secret in SECRETS:
        assert not any(secret in line for line in lines), secret     # ... without its secret


def test_the_console_says_boss_s_words(tmp_path):
    said = {
        "denied": _pair(Fake(polls=[(403, {})]))[1],
        "expired": _pair(Fake(polls=[(410, {})]))[1],
        "unavailable": _pair(Fake(polls=[(502, {})]))[1],
        "other": _pair(Fake(polls=[(418, {"error": "teapot"})]))[1],
        "key": _pair(Fake(keys_status=401))[1],
    }
    assert "On your phone, open Windy and approve this computer. If it asks for a code, type 1234-5678." in said["denied"]
    assert "The pairing was declined on your phone. Nothing was changed." in said["denied"]
    assert "The code expired before it was approved. Run this again for a new code." in said["expired"]
    assert ("Windy couldn't finish the move, so your agent isn't running anywhere right now. Run this again for a new "
            "code to finish it." in said["unavailable"])
    assert "Windy Connect didn't accept this pairing. Run this again for a new code. (Reason: teapot)" in said["other"]
    assert ("Eternitas didn't accept this computer's key, so Pip couldn't move here, and it isn't running anywhere "
            "right now. Run this again for a new code to finish the move." in said["key"])


def test_ctrl_c_cancels_the_code(tmp_path):
    fake = Fake()

    def interrupted(_s):
        raise KeyboardInterrupt

    buf, out = io.StringIO(), io.StringIO()
    rc = home_pair.pair(Console(file=buf, color_system=None), transport=fake.transport(), sleep=interrupted,
                        out=out)
    said = " ".join(buf.getvalue().split())
    assert rc == 130 and "/v1/home/cancel" in fake.paths()
    assert "Cancelled. If you already approved on your phone, run this again to finish." in said
    sent = next(c for c in fake.calls if c[0] == "/v1/home/code")[2]["agent_kid"]
    cancel = next(c for c in fake.calls if c[0] == "/v1/home/cancel")
    assert cancel[2] == {"device_code": DEVICE, "agent_kid": sent}
    assert not (tmp_path / "state" / "credentials.json").exists()   # cancelled = nothing changed


def test_a_key_only_agent_still_takes_the_runtime_claim(monkeypatch):
    """A paired computer has no long-lived token: the claim goes out with the key's EPT+agent, never skipped."""
    from windyfly import runtime_claim

    monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)
    monkeypatch.delenv("WINDY_JWT", raising=False)
    monkeypatch.setattr(runtime_claim, "_key_only", lambda: True)
    assert runtime_claim._read_creds() == (PASSPORT, "")
    monkeypatch.setattr(runtime_claim, "_key_only", lambda: False)
    assert runtime_claim._read_creds() is None


# ── Hub's #700 review ────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_computer_with_another_agent_refuses_before_any_code(tmp_path, monkeypatch):
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-OTHER-0001")
    fake = Fake()
    rc, said, _ = _pair(fake)
    assert rc == 1 and fake.calls == []
    assert ("An agent is already set up on this computer, so another one can't be moved here. To bring a different "
            "agent home, use another computer.") in " ".join(said.split())


def test_another_agent_s_registered_key_is_never_handed_to_a_new_one(tmp_path):
    ak.local_kid()
    data = ak.load_credentials()
    entry = ak.active_entry(data)
    entry.update(registered_at="2026-11-01T00:00:00Z", passport="ET26-OTHER-0001")
    ak.save_credentials(data)
    out = ak.register_with_bootstrap(PASSPORT, BOOT, transport=Fake().transport())
    assert out == {"status": "failed", "code": "key_belongs_to_other_agent"}
    assert ak.register_with_bootstrap("ET26-OTHER-0001", BOOT, transport=Fake().transport())["status"] == \
        "already_registered"


class _KeyExists(Fake):
    def __init__(self, listed: bool, **kw):
        super().__init__(**kw)
        self.listed = listed

    def handle(self, req):
        if req.url.path == f"/api/v1/bots/{PASSPORT}/keys":
            if req.method == "POST":
                self.calls.append((req.url.path, "POST", {}, dict(req.headers)))
                return httpx.Response(409, json={"detail": {"code": "key_exists", "message": "already"}})
            sent = next(c for c in self.calls if c[0] == "/v1/home/code")[2]["agent_kid"]  # never the key lock here
            kids = [{"kid": sent, "status": "active"}] if self.listed else []
            return httpx.Response(200, json={"keys": kids})
        return super().handle(req)


def test_a_lost_registration_answer_is_settled_by_reading_the_keys(tmp_path):
    assert _pair(_KeyExists(listed=True))[0] == 0 and ak.has_registered_key()


def test_key_exists_for_a_key_eternitas_does_not_list_ends_the_round_and_undoes(tmp_path):
    assert _pair(_KeyExists(listed=False))[0] == 1
    assert not (tmp_path / "state" / "credentials.json").exists()


def test_a_memory_whose_hash_does_not_match_is_not_imported_but_the_agent_is_home(tmp_path):
    from windyfly.memory.database import Database

    fake = Fake()
    real = fake.handle

    def handle(req):
        if req.url.path == "/v1/home/poll" and fake.polls and fake.polls[0][0] == 200:
            fake.polls.pop(0)
            return httpx.Response(200, json={"passport_number": PASSPORT, "agent_name": "Pip", "bootstrap_token": BOOT,
                                             "memory": {"url": f"https://connect.test/v1/device/memory?claim={CLAIM}",
                                                        "sha256": "0" * 64}})
        return real(req)

    fake.handle = handle
    rc, said, _ = _pair(fake)
    assert rc == 0 and "Pip's memory couldn't be brought over, so it starts here without it" in said
    db = Database(str(tmp_path / "windyfly.db"))
    assert db.fetchone("SELECT COUNT(*) AS c FROM episodes")["c"] == 0
    db.close()
    assert list((tmp_path / "state").glob("memory-*.rejected.ndjson"))
    assert not list((tmp_path / "state").glob("memory-*[0-9].ndjson"))


def test_a_memory_that_does_not_decode_is_reported_not_raised(tmp_path):
    fake = Fake(memory=(200, {"format": "ndjson-v1", "inline": "H4sIAAAA"}))   # a truncated gzip
    rc, said, out = _pair(fake, json_status=True)
    events = [json.loads(line) for line in out.splitlines()]
    assert rc == 0 and events[-2]["event"] == "memory" and events[-2]["status"] == "decode_failed"


@pytest.mark.parametrize("where", ["code", "poll"])
def test_a_non_json_200_is_an_error_event_not_a_traceback(tmp_path, where):
    fake = Fake()
    real = fake.handle

    def handle(req):
        if req.url.path == f"/v1/home/{where}":
            fake.calls.append((req.url.path, req.method, {}, {}))
            return httpx.Response(200, text="<html>Hotel Wi-Fi login</html>")
        return real(req)

    fake.handle = handle
    rc, _, out = _pair(fake, json_status=True)
    assert rc == 1 and json.loads(out.splitlines()[-1]) == {"event": "error", "code": "bad_response"}
    assert not (tmp_path / "state" / "credentials.json").exists()


def test_json_status_always_ends_with_an_event_even_on_a_bug(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(home_pair, "settle_home", boom)
    rc, _, out = _pair(Fake(), json_status=True)
    assert rc == 1 and json.loads(out.splitlines()[-1]) == {"event": "error", "code": "internal",
                                                             "detail": "RuntimeError"}


def test_an_agent_name_is_shown_as_text_never_as_markup(tmp_path):
    fake = Fake()
    real = fake.handle

    def handle(req):
        if req.url.path == "/v1/home/poll" and fake.polls and fake.polls[0][0] == 200:
            fake.polls.pop(0)
            return httpx.Response(200, json={"passport_number": PASSPORT, "agent_name": "[red]Pip[/red]",
                                             "bootstrap_token": BOOT, "memory": None})
        return real(req)

    fake.handle = handle
    rc, said, _ = _pair(fake)
    assert rc == 0 and "[red]Pip[/red] now lives on this computer" in said


def test_a_key_only_claim_goes_out_through_a_real_transport_without_an_empty_bearer(monkeypatch, tmp_path):
    """Hub on #700: "Authorization: Bearer " (empty) is an illegal header; h11 raised LocalProtocolError, so the claim
    ran DEGRADED and counted a legacy auth. MockTransport skips h11's check, so this goes through a real socket."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from windyfly import runtime_claim
    from windyfly.observability import agent_health

    seen: list[dict] = []

    class Mind(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen.append(dict(self.headers))
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = b'{"claimed": true, "ttl_seconds": 90}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Mind)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    legacy: list[int] = []
    try:
        monkeypatch.setenv("WINDYFLY_HOME", str(tmp_path))   # the claim record goes here, never the checkout
        monkeypatch.setenv("MIND_API_URL", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)
        monkeypatch.delenv("WINDY_JWT", raising=False)
        monkeypatch.setattr(runtime_claim, "_key_only", lambda: True)
        monkeypatch.setattr(agent_health, "note_mind_legacy_auth", lambda: legacy.append(1))
        runtime_claim._reset_state_for_tests()
        assert runtime_claim.acquire_runtime_slot(source="matrix") == runtime_claim.ClaimOutcome.GRANTED
    finally:
        runtime_claim._reset_state_for_tests()
        server.shutdown()
        server.server_close()
    assert seen and not any(k.lower() == "authorization" for k in seen[0])
    assert legacy == []


@pytest.mark.parametrize("name", ['Pip', 'Pip "the" Fly', "back\\slash", "Ünïcode 🪰", "line\nbreak"])
def test_an_agent_name_can_never_break_the_config_file(name):
    import tomllib

    from windyfly.quickstart import _toml_str

    assert tomllib.loads(f"name = {_toml_str(name)}\n")["name"] == name


def test_a_download_past_the_size_cap_is_refused(tmp_path, monkeypatch):
    from windyfly import bring_home

    monkeypatch.setattr(bring_home, "MAX_MEMORY_BYTES", 64)    # the download itself is bigger than this
    rc, _, out = _pair(Fake(), json_status=True)
    events = [json.loads(line) for line in out.splitlines()]
    assert rc == 0 and events[-2]["status"] == "download_failed"


def test_a_small_download_that_unzips_past_the_cap_is_refused_not_unzipped(monkeypatch):
    import base64
    import gzip

    from windyfly import bring_home

    bomb = base64.b64encode(gzip.compress(b"x" * 100_000)).decode()   # ~0.2 KB that unzips to 100 KB
    monkeypatch.setattr(bring_home, "MAX_MEMORY_BYTES", 10_000)
    with pytest.raises(bring_home.MemoryTooLarge):
        bring_home.decode_memory({"inline": bomb})


def test_a_new_memory_database_is_owner_only_and_an_old_one_keeps_its_mode(tmp_path):
    import stat

    from windyfly.memory.database import Database

    fresh = tmp_path / "new" / "windyfly.db"
    Database(str(fresh)).close()
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600
    old = tmp_path / "old.db"
    Database(str(old)).close()
    old.chmod(0o640)
    Database(str(old)).close()
    assert stat.S_IMODE(old.stat().st_mode) == 0o640


# ── Hub's second #700 read ───────────────────────────────────────────────────────────────────────────────────────────

def _cloud_record(name="Pip"):
    hub_hatch._write_private_json(hub_hatch.cloud_agent_path(), {"passport_number": PASSPORT, "agent_name": name,
                                                                  "where": "cloud"})


def _pair_asked(fake, answer, **kw):
    buf, out = io.StringIO(), io.StringIO()
    t = {"now": 1000.0}
    asked: list[str] = []

    def ask(q):
        asked.append(q)
        return answer

    def sleep(s):
        t["now"] += s

    rc = home_pair.pair(Console(file=buf, width=300, color_system=None), transport=fake.transport(), sleep=sleep,
                        now=lambda: t["now"], out=out, ask=ask, **kw)
    return rc, buf.getvalue(), asked


def test_n1_a_cloud_record_asks_once_and_no_changes_nothing(tmp_path):
    _cloud_record()
    fake = Fake()
    rc, said, asked = _pair_asked(fake, "n")
    assert rc == 1 and fake.calls == [] and "Cancelled. Nothing was changed." in said
    assert asked == ["  This computer is set up to talk to Pip in the cloud. If you bring a different agent home here, "
                     "that link is replaced; Pip keeps running in the cloud. Continue? [y/N] "]
    assert hub_hatch.cloud_agent()["where"] == "cloud"


def test_n1_a_nameless_cloud_record_asks_the_nameless_question(tmp_path):
    _cloud_record(name="")
    rc, _, asked = _pair_asked(Fake(), "n")
    assert asked == ["  This computer is set up to talk to an agent in the cloud. If you bring a different agent home "
                     "here, that link is replaced; that agent keeps running in the cloud. Continue? [y/N] "]


def test_n1_yes_brings_the_agent_home_and_keeps_the_old_record_as_a_dated_backup(tmp_path):
    _cloud_record()
    rc, said, _ = _pair_asked(Fake(), "y")
    assert rc == 0 and hub_hatch.cloud_agent()["where"] == "home"
    backups = list(hub_hatch.cloud_agent_path().parent.glob("cloud_agent.json.bak-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text())["where"] == "cloud"


def test_n1_json_status_needs_yes_for_a_cloud_record(tmp_path):
    _cloud_record()
    fake = Fake()
    rc, _, out = _pair(fake, json_status=True)
    assert rc == 1 and json.loads(out) == {"event": "error", "code": "cloud_link_confirm_needed", "agent_name": "Pip"}
    assert fake.calls == []
    assert _pair(Fake(), json_status=True, yes=True)[0] == 0


def test_n1_no_record_proceeds_without_a_question(tmp_path):
    rc, _, asked = _pair_asked(Fake(), "n")
    assert rc == 0 and asked == []


def test_n1_a_registered_key_on_this_computer_blocks(tmp_path):
    ak.local_kid()
    data = ak.load_credentials()
    ak.active_entry(data).update(registered_at="2026-11-01T00:00:00Z", passport="ET26-OTHER-0001")
    ak.save_credentials(data)
    fake = Fake()
    rc, _, out = _pair(fake, json_status=True)
    assert rc == 1 and json.loads(out)["code"] == "another_agent_here" and fake.calls == []


def test_n2_an_incomplete_pickup_says_the_agent_is_running_nowhere(tmp_path):
    fake = Fake(polls=[(200, {"passport_number": PASSPORT, "agent_name": "Pip"})])   # no bootstrap token
    rc, said, _ = _pair(fake)
    assert rc == 1 and "Nothing was changed" not in said
    assert ("Windy couldn't finish the move, so Pip isn't running anywhere right now. Run this again for a new code "
            "to finish it.") in " ".join(said.split())


class _LostReply(Fake):
    def handle(self, req):
        if req.url.path == f"/api/v1/bots/{PASSPORT}/keys" and req.method == "POST":
            raise httpx.ReadTimeout("the answer got lost", request=req)
        return super().handle(req)


def test_n3_a_lost_registration_answer_keeps_the_key_and_the_next_round_settles_it(tmp_path):
    first = _LostReply()
    rc, said, _ = _pair(first)
    flat = " ".join(said.split())
    assert rc == 1 and "isn't running anywhere right now" in flat and "Eternitas didn't accept" not in flat
    sent = next(c for c in first.calls if c[0] == "/v1/home/code")[2]["agent_kid"]
    assert (tmp_path / "state" / "credentials.json").exists()            # read BEFORE anything could make a key
    entry = ak.active_entry(ak.load_credentials())
    assert entry["kid"] == sent and not entry.get("registered_at")
    second = _KeyExists(listed=True)
    assert _pair(second)[0] == 0 and ak.has_registered_key()
    assert next(c for c in second.calls if c[0] == "/v1/home/code")[2]["agent_kid"] == sent


def test_n4_a_round_that_stopped_after_registering_is_finished_by_the_next_run(tmp_path, monkeypatch):
    from windyfly import bring_home

    real = bring_home.upsert_env
    monkeypatch.setattr(bring_home, "upsert_env", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    rc, _, out = _pair(Fake(), json_status=True)
    assert rc == 1 and json.loads(out.splitlines()[-1])["code"] == "internal" and ak.has_registered_key()
    monkeypatch.setattr(bring_home, "upsert_env", real)
    fake = Fake()
    rc, said, _ = _pair(fake)
    assert rc == 0 and fake.calls == []                               # finished from the marker, no new round
    # the memory WAS imported before the stop: the marker carries that, so the resume says it (Hub on #700)
    assert "messages carried over" in said and "couldn't be brought over" not in said
    assert "Pip now lives on this computer" in said
    assert hub_hatch.cloud_agent()["where"] == "home" and not (tmp_path / "state" / "home-pair-pending.json").exists()


def test_n6_an_empty_memory_with_a_declared_count_is_rejected_not_imported(tmp_path):
    import base64
    import gzip

    empty = {"format": "ndjson-v1", "inline": base64.b64encode(gzip.compress(b"")).decode(), "event_count": 5}
    rc, _, out = _pair(Fake(memory=(200, empty)), json_status=True)
    memory = next(json.loads(line) for line in out.splitlines() if json.loads(line)["event"] == "memory")
    assert rc == 0 and memory["status"] == "count_mismatch"


def test_n5_a_quarantined_database_is_recreated_owner_only(tmp_path):
    import stat

    from windyfly.memory.database import Database

    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a database at all" * 10)
    bad.chmod(0o644)
    db = Database(str(bad))
    assert db.quarantined_from is not None and stat.S_IMODE(bad.stat().st_mode) == 0o600
    db.close()


def test_ctrl_c_during_the_code_request_still_ends_with_the_cancel_line(tmp_path):
    fake = Fake()
    real = fake.handle

    def handle(req):
        if req.url.path == "/v1/home/code":
            raise KeyboardInterrupt
        return real(req)

    fake.handle = handle
    rc, said, _ = _pair(fake)
    assert rc == 130 and "Cancelled. If you already approved on your phone, run this again to finish." in \
        " ".join(said.split())
    assert not (tmp_path / "state" / "credentials.json").exists()


def test_the_user_code_is_shown_as_text_never_as_markup(tmp_path):
    fake = Fake(polls=[(403, {})])
    real = fake.handle

    def handle(req):
        if req.url.path == "/v1/home/code":
            return httpx.Response(200, json={"device_code": DEVICE, "user_code": "[red]1234[/red]", "expires_in": 300,
                                             "interval": 5})
        return real(req)

    fake.handle = handle
    assert "type [red]1234[/red]." in _pair(fake)[1]


def test_a_key_only_claim_carries_the_key_s_ept_agent_and_dpop(monkeypatch, tmp_path):
    """Hub on #700: with Mind auth ON and a registered key, the claim really goes out authenticated (Authorization +
    DPoP), through a real socket. The token is minted by a local stand-in for Eternitas, bound to this key (cnf.jkt)."""
    import base64
    import threading
    import time as _time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from windyfly import runtime_claim

    kid = ak.local_kid()
    data = ak.load_credentials()
    ak.active_entry(data).update(registered_at="2026-11-03T00:00:00Z", passport=PASSPORT)
    ak.save_credentials(data)
    b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    token = f"{b64({'alg': 'ES256'})}.{b64({'sub': PASSPORT, 'cnf': {'jkt': kid}, 'exp': int(_time.time()) + 300})}.sig"
    seen: dict[str, dict] = {}

    class Both(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen[self.path] = dict(self.headers)
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.dumps({"token": token, "token_type": "EPT+agent", "expires_in": 300} if "tokens" in self.path
                              else {"claimed": True, "ttl_seconds": 90}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Both)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setenv("WINDYFLY_HOME", str(tmp_path))
        monkeypatch.setenv("MIND_API_URL", base)
        monkeypatch.setenv("ETERNITAS_URL", base)
        monkeypatch.setenv("WINDY_MIND_EPT_AGENT", "1")
        monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)
        monkeypatch.delenv("WINDY_JWT", raising=False)
        ak.clear_token_cache()
        runtime_claim._reset_state_for_tests()
        assert runtime_claim.acquire_runtime_slot(source="matrix") == runtime_claim.ClaimOutcome.GRANTED
    finally:
        runtime_claim._reset_state_for_tests()
        ak.clear_token_cache()
        server.shutdown()
        server.server_close()
    claim = seen["/v1/runtime/claim"]
    assert claim.get("Authorization") == f"Bearer {token}" and claim.get("DPoP")



# ── Hub's non-blocking #700 follow-ups ───────────────────────────────────────────────────────────────────────────────

def test_f1_a_bring_home_partway_done_says_finish_it_with_bring_home(tmp_path):
    from windyfly import bring_home

    _cloud_record()
    bring_home._atomic_write(bring_home._seal_path(PASSPORT), b'{"sealed": true}')
    fake = Fake()
    rc, said, _ = _pair(fake, yes=True)
    assert rc == 1 and fake.calls == [] and not ak.load_credentials()
    assert ("A move of this agent with windy bring-home is partway done on this computer. To finish it, run: windy "
            "bring-home") in " ".join(said.split())


def test_f2_the_marker_carries_the_memory_outcome(tmp_path, monkeypatch):
    seen: list[dict] = []
    real = home_pair._finish

    def spy(pending, **kw):
        seen.append(json.loads((tmp_path / "state" / "home-pair-pending.json").read_text()))
        return real(pending, **kw)

    monkeypatch.setattr(home_pair, "_finish", spy)
    assert _pair(Fake())[0] == 0
    assert seen[0]["memory"]["status"] == "imported" and seen[0]["memory"]["imported"] > 0
    assert not (tmp_path / "state" / "home-pair-pending.json").exists()   # cleared at the end, as before


class _Unreachable(Fake):
    def handle(self, req):
        if req.url.path == f"/api/v1/bots/{PASSPORT}/keys" and req.method == "POST":
            raise httpx.ConnectError("no route", request=req)
        return super().handle(req)


def test_f3_eternitas_never_reached_is_the_running_nowhere_line_not_a_refused_key(tmp_path):
    rc, said, _ = _pair(_Unreachable())
    flat = " ".join(said.split())
    assert rc == 1 and "Eternitas didn't accept" not in flat
    assert ("Windy couldn't finish the move, so Pip isn't running anywhere right now. Run this again for a new code "
            "to finish it.") in flat
    assert not ak.has_registered_key() and not (tmp_path / "state" / "home-pair-pending.json").exists()


@pytest.mark.parametrize("stop", [KeyboardInterrupt, EOFError])
def test_f4a_ctrl_c_at_the_question_cancels_with_nothing_changed(tmp_path, stop):
    _cloud_record()
    fake = Fake()
    buf, out = io.StringIO(), io.StringIO()

    def ask(q):
        raise stop

    rc = home_pair.pair(Console(file=buf, width=300, color_system=None), transport=fake.transport(),
                        sleep=lambda s: None, now=lambda: 1000.0, out=out, ask=ask)
    assert rc == 130 and fake.calls == [] and "Cancelled. Nothing was changed." in buf.getvalue()
    assert not ak.load_credentials()


class _CtrlCWhileRegistering(Fake):
    def handle(self, req):
        if req.url.path == f"/api/v1/bots/{PASSPORT}/keys" and req.method == "POST":
            raise KeyboardInterrupt
        return super().handle(req)


def test_f4b_ctrl_c_while_registering_keeps_the_key_for_the_next_run(tmp_path):
    first = _CtrlCWhileRegistering()
    rc, _, _ = _pair(first)
    assert rc == 130
    sent = next(c for c in first.calls if c[0] == "/v1/home/code")[2]["agent_kid"]
    assert ak.active_entry(ak.load_credentials())["kid"] == sent        # kept: Eternitas may already hold it
    second = _KeyExists(listed=True)
    assert _pair(second)[0] == 0 and ak.has_registered_key()
    assert next(c for c in second.calls if c[0] == "/v1/home/code")[2]["agent_kid"] == sent


def test_f5_a_passport_in_the_agent_env_file_refuses_another_agent(tmp_path, monkeypatch):
    """The env-file branch: the passport comes from the agent's env file, not the process environment."""
    import dotenv
    from dotenv.main import load_dotenv as real_load_dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", real_load_dotenv)       # the suite turns it off; this test needs it
    (tmp_path / "agent.env").write_text("KEEP_ME=1\nETERNITAS_PASSPORT=ET26-OTHER-0001\n", encoding="utf-8")
    assert "ETERNITAS_PASSPORT" not in os.environ
    fake = Fake()
    rc, _, out = _pair(fake, json_status=True)
    assert rc == 1 and json.loads(out)["code"] == "another_agent_here" and fake.calls == []


def test_f6_the_memory_line_shows_where_the_memory_was_saved(tmp_path):
    rc, said, _ = _pair(Fake())
    flat = " ".join(said.split())
    assert rc == 0 and f"messages carried over, saved in {tmp_path / 'windyfly.db'}." in flat


def test_f6_json_status_carries_the_memory_path(tmp_path):
    rc, _, out = _pair(Fake(), json_status=True)
    assert rc == 0
    memory = next(json.loads(line) for line in out.splitlines() if json.loads(line)["event"] == "memory")
    assert memory["db_path"] == str(tmp_path / "windyfly.db")
