"""Eternitas agent-keys v1 (windy-agent side): keys, file, PoP, register,
rotate, owner reset, revoke, artifact signing — against a fake Eternitas."""

from __future__ import annotations

import argparse
import base64
import inspect
import json
import logging
import re
import stat
import time
import uuid

import httpx
import pytest

from windyfly.eternitas import agent_keys as ak

PASSPORT = "ET26-TEST-KEY1"


def _jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


EPT = _jwt({"sub": PASSPORT, "exp": int(time.time()) + 86400})


class FakeEternitas:
    """Just enough of the agent-keys v1 routes to exercise the client."""

    def __init__(self, *, deployed: bool = True) -> None:
        self.deployed = deployed
        self.nonces: set[str] = set()
        self.keys: dict[str, dict] = {}
        self.calls: list[tuple[str, str, str]] = []  # (method, path, bearer)
        self.register_status: int | None = None
        self.registered_via: dict[str, str] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        bearer = req.headers.get("authorization", "").removeprefix("Bearer ")
        self.calls.append((req.method, path, bearer))
        if not self.deployed:
            return httpx.Response(404, json={"detail": "Not Found"})
        m = re.fullmatch(r"/api/v1/bots/([^/]+)/keys(?:/(challenge|[^/]+)(?:/(retire|revoke))?)?", path)
        if not m:
            return httpx.Response(404, json={"detail": "Not Found"})
        passport, sub, verb = m.groups()
        if passport != PASSPORT:
            return httpx.Response(404, json={"detail": "passport not found"})
        if req.method == "GET" and sub is None:
            return httpx.Response(200, json={"keys": [
                {**k["jwk"], "status": k["status"]} for k in self.keys.values()]})
        if sub == "challenge":
            n = uuid.uuid4().hex
            self.nonces.add(n)
            return httpx.Response(201, json={"nonce": n, "expires_in": 300})
        if sub is None:  # register
            if self.register_status:
                return httpx.Response(self.register_status, json={"detail": "limited"})
            body = json.loads(req.content)
            jwk, proof = body["jwk"], body["proof"]
            kid = ak.thumbprint(jwk)
            assert jwk["kid"] == kid and jwk["alg"] == "ES256" and jwk["use"] == "sig"
            v = ak.verify_jws(proof, jwk)
            assert v is not None, "PoP must verify against the new key"
            claims = json.loads(v["payload"])
            assert v["header"]["kid"] == kid and v["header"]["passport"] == PASSPORT
            assert claims["passport"] == PASSPORT
            assert claims["nonce"] in self.nonces, "nonce must be single use"
            self.nonces.discard(claims["nonce"])
            assert abs(claims["iat"] - time.time()) < 60
            self.keys[kid] = {"jwk": jwk, "status": "active"}
            self.registered_via[kid] = "own_ept" if bearer == EPT else "owner_recovery"
            return httpx.Response(201, json={"kid": kid, "status": "active"})
        kid = sub
        if kid not in self.keys:
            return httpx.Response(404, json={"detail": "key not found"})
        self.keys[kid]["status"] = "retired" if verb == "retire" else "revoked"
        return httpx.Response(200, json={"kid": kid, "status": self.keys[kid]["status"]})


@pytest.fixture
def env(tmp_path, monkeypatch):
    creds = tmp_path / "agent" / "credentials.json"
    monkeypatch.setenv("WINDY_CREDENTIALS_FILE", str(creds))
    monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", EPT)
    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")
    monkeypatch.setattr(ak, "_UNSUPPORTED_LOGGED", False)
    # A stored hub session must never be used for owner registration.
    monkeypatch.setattr(ak, "stored_hub_token", lambda: "STORED-SESSION-TOKEN")
    return creds


# ── crypto primitives ───────────────────────────────────────────────

def test_rfc7638_thumbprint_vector():
    # RFC 7638 §3.1
    jwk = {
        "kty": "RSA",
        "n": "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_BJECP"
             "ebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_FDW2QvzqY"
             "368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0f"
             "M4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw",
        "e": "AQAB",
        "alg": "RS256",
        "kid": "2011-04-29",
    }
    assert ak.thumbprint(jwk) == "NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs"


def test_ec_thumbprint_ignores_optional_members():
    key = ak.generate_private_key()
    jwk = ak.public_jwk(key)
    assert ak.thumbprint(jwk) == ak.thumbprint(ak.published_jwk(key))
    assert len(base64.urlsafe_b64decode(jwk["x"] + "=")) == 32


def test_pop_verifies_and_binds_passport_and_nonce():
    key = ak.generate_private_key()
    jws = ak.registration_proof(key, PASSPORT, "n-1")
    v = ak.verify_jws(jws, ak.public_jwk(key))
    assert v is not None
    assert v["header"] == {"alg": "ES256", "typ": "JWT", "kid": ak.thumbprint(ak.public_jwk(key)),
                           "passport": PASSPORT}
    assert json.loads(v["payload"])["nonce"] == "n-1"
    other = ak.generate_private_key()
    assert ak.verify_jws(jws, ak.public_jwk(other)) is None


def test_platform_pop_and_dpop_shapes():
    key = ak.generate_private_key()
    v = ak.verify_jws(ak.platform_pop(key, PASSPORT, "drops", "n"), ak.public_jwk(key))
    assert v["header"]["typ"] == "eternitas-pop+jwt"
    c = json.loads(v["payload"])
    assert c["iss"] == PASSPORT and c["aud"] == "drops" and c["exp"] - c["iat"] <= 60
    d = ak.verify_jws(ak.dpop_proof(key, "post", "https://x/y"), ak.public_jwk(key))
    assert d["header"]["typ"] == "dpop+jwt" and d["header"]["jwk"] == ak.public_jwk(key)
    assert json.loads(d["payload"])["htm"] == "POST"


# ── credentials file ────────────────────────────────────────────────

def test_file_schema_mode_preservation_and_backup(env):
    env.parent.mkdir(parents=True)
    env.write_text(json.dumps({"other": {"keep": 1}}))
    fake = FakeEternitas()
    assert ak.ensure_registered(transport=fake.transport())["status"] == "registered"

    data = json.loads(env.read_text())
    assert data["other"] == {"keep": 1}
    sec = data["eternitas"]
    assert set(sec) == {"private_key", "kid", "keys"}
    assert sec["private_key"].startswith("-----BEGIN PRIVATE KEY-----")
    (entry,) = sec["keys"]
    assert entry["kid"] == sec["kid"] and entry["private_key"] == sec["private_key"]
    assert entry["status"] == "active" and entry["created_at"] and entry["registered_at"]
    assert ak.thumbprint(ak.public_jwk(ak.load_private_key(sec["private_key"]))) == sec["kid"]

    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    backups = list(env.parent.glob("credentials.json.bak-*"))
    assert backups, "each write keeps a timestamped backup"
    assert all(stat.S_IMODE(b.stat().st_mode) == 0o600 for b in backups)
    assert not list(env.parent.glob(".credentials.json.*")), "no temp files left behind"


def test_save_is_atomic_on_failure(env, monkeypatch):
    ak.save_credentials({"a": 1})
    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(ak.os, "replace", boom)
    with pytest.raises(OSError):
        ak.save_credentials({"a": 2})
    assert json.loads(env.read_text()) == {"a": 1}
    assert not list(env.parent.glob(".credentials.json.*"))


def test_default_path_is_state_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("WINDY_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
    assert ak.credentials_path() == tmp_path / "credentials.json"


# ── register / skip ─────────────────────────────────────────────────

def test_register_happy_path_then_idempotent(env):
    fake = FakeEternitas()
    r = ak.ensure_registered(transport=fake.transport())
    assert r["status"] == "registered" and r["via"] == "own_ept"
    assert fake.keys[r["kid"]]["status"] == "active"
    assert all(b == EPT for _, _, b in fake.calls)
    n = len(fake.calls)
    assert ak.ensure_registered(transport=fake.transport())["status"] == "already_registered"
    assert len(fake.calls) == n


def test_unsupported_404_is_quiet_and_keeps_the_key(env, caplog):
    fake = FakeEternitas(deployed=False)
    with caplog.at_level(logging.INFO, logger=ak.__name__):
        assert ak.ensure_registered(transport=fake.transport())["status"] == "unsupported"
        assert ak.ensure_registered(transport=fake.transport())["status"] == "unsupported"
    infos = [r for r in caplog.records if "not yet supported" in r.getMessage() and r.levelno == logging.INFO]
    assert len(infos) == 1
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    kid = json.loads(env.read_text())["eternitas"]["kid"]
    # Once Eternitas ships, the SAME key is registered.
    fake.deployed = True
    assert ak.ensure_registered(transport=fake.transport())["kid"] == kid


def test_unknown_passport_404_is_a_failure_not_unsupported(env, monkeypatch):
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-NOPE-NOPE")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _jwt({"sub": "ET26-NOPE-NOPE", "exp": time.time() + 99}))
    assert ak.ensure_registered(transport=FakeEternitas().transport())["status"] == "failed"


def test_no_passport_and_expired_ept(env, monkeypatch):
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _jwt({"sub": PASSPORT, "exp": 1}))
    fake = FakeEternitas()
    r = ak.ensure_registered(transport=fake.transport())
    assert r["status"] == "needs_login" and not fake.calls  # no silent owner fallback
    monkeypatch.delenv("ETERNITAS_PASSPORT")
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN")
    assert ak.ensure_registered()["status"] == "no_passport"


def test_background_and_hooks_skip_under_pytest(env, monkeypatch):
    assert ak.ensure_in_background() is None
    assert ak.register_after_signin() == {"status": "skipped"}
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.setenv("WINDY_DISABLE_AGENT_KEYS", "1")
    assert ak.ensure_in_background() is None
    assert not env.exists()


def test_boot_step_and_daily_job_are_wired():
    from windyfly.agent import boot, maintenance
    names = [s.name for s in boot.default_capability_registration_sequence()]
    assert "eternitas.agent_keys" in names
    src = inspect.getsource(maintenance)
    assert 'name="eternitas.agent_keys.daily"' in src


# ── rotate / reset / revoke ─────────────────────────────────────────

def test_rotate_keeps_two_then_retires_old(env):
    fake = FakeEternitas()
    old = ak.ensure_registered(transport=fake.transport())["kid"]
    r = ak.rotate(transport=fake.transport())
    assert r["status"] == "rotated" and r["old_kid"] == old
    assert fake.keys[old]["status"] == "retired" and fake.keys[r["kid"]]["status"] == "active"
    sec = json.loads(env.read_text())["eternitas"]
    assert sec["kid"] == r["kid"] and [k["kid"] for k in sec["keys"]] == [r["kid"]]


def test_rotate_overlap_survives_failed_retire(env):
    fake = FakeEternitas()
    old = ak.ensure_registered(transport=fake.transport())["kid"]
    orig = fake.handle

    def no_retire(req):
        if req.url.path.endswith("/retire"):
            return httpx.Response(503, json={"detail": "down"})
        return orig(req)
    r = ak.rotate(transport=httpx.MockTransport(no_retire))
    sec = json.loads(env.read_text())["eternitas"]
    assert [(k["kid"], k["status"]) for k in sec["keys"]] == [(old, "retiring"), (r["kid"], "active")]
    assert sec["kid"] == r["kid"]
    # the daily job finishes the retire
    assert ak.ensure_registered(transport=fake.transport())["status"] == "registered"
    assert [k["kid"] for k in json.loads(env.read_text())["eternitas"]["keys"]] == [r["kid"]]
    assert fake.keys[old]["status"] == "retired"


def test_reset_uses_fresh_prompt_login_token_never_stored_session(env, monkeypatch):
    fake = FakeEternitas()
    old = ak.ensure_registered(transport=fake.transport())["kid"]
    # Eternitas also knows a key from a lost device that isn't in this file.
    lost = ak.published_jwk(ak.generate_private_key())
    fake.keys[lost["kid"]] = {"jwk": lost, "status": "active"}
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _jwt({"sub": PASSPORT, "exp": 1}))  # lapsed

    from windyfly import hub_login
    seen = {}

    def fake_login(**kw):
        seen.update(kw)
        return {"windy_identity_id": "wid", "access_token": "FRESH-HUB-TOKEN"}
    monkeypatch.setattr(hub_login, "login", fake_login)
    def no_stored(*_a, **_k):
        raise AssertionError("reset must not read the stored session")
    monkeypatch.setattr(hub_login, "get_access_token", no_stored)
    monkeypatch.setattr(ak, "stored_hub_token", no_stored)

    fake.calls.clear()
    r = ak.reset(transport=fake.transport(), owner_token=lambda: ak.fresh_owner_token(open_browser=False))
    assert r["status"] == "reset"
    assert seen["reauth"] is True and seen["store"] is False
    assert {b for m, _p, b in fake.calls if m == "POST"} == {"FRESH-HUB-TOKEN"}
    assert fake.registered_via[r["kid"]] == "owner_recovery"
    assert sorted(r["revoked"]) == sorted([old, lost["kid"]])
    assert fake.keys[old]["status"] == fake.keys[lost["kid"]]["status"] == "revoked"
    assert json.loads(env.read_text())["eternitas"]["kid"] == r["kid"]


def test_reset_authorize_url_forces_reauth():
    from windyfly import hub_login
    url = hub_login.build_authorize_url("http://127.0.0.1:1/cb", "s", "c", reauth=True)
    assert "prompt=login" in url and "max_age=0" in url
    assert "prompt=" not in hub_login.build_authorize_url("http://127.0.0.1:1/cb", "s", "c")


def test_reset_rate_limited_keeps_old_key(env):
    fake = FakeEternitas()
    old = ak.ensure_registered(transport=fake.transport())["kid"]
    fake.register_status = 429
    r = ak.reset(transport=fake.transport(), owner_token=lambda: "FRESH")
    assert r["status"] == "rate_limited"
    assert fake.keys[old]["status"] == "active"
    assert json.loads(env.read_text())["eternitas"]["kid"] == old


def test_reset_cli_says_once_per_24h(env, monkeypatch, capsys):
    from windyfly.commands import agent_key
    monkeypatch.setattr(ak, "reset", lambda **_k: {"status": "rate_limited"})
    args = argparse.Namespace(action="reset", yes=False, no_browser=True)
    assert agent_key.cmd_agent_key(args, ask=lambda _q: "y") == 1
    assert "once per 24h" in capsys.readouterr().out
    assert agent_key.cmd_agent_key(args, ask=lambda _q: "") == 1  # default N


def test_revoke_active_key_then_next_ensure_makes_new(env):
    fake = FakeEternitas()
    kid = ak.ensure_registered(transport=fake.transport())["kid"]
    r = ak.revoke(kid, transport=fake.transport())
    assert r["status"] == "revoked" and r["was_active"]
    assert fake.keys[kid]["status"] == "revoked"
    sec = json.loads(env.read_text())["eternitas"]
    assert sec["keys"] == [] and "kid" not in sec
    new = ak.ensure_registered(transport=fake.transport())
    assert new["status"] == "registered" and new["kid"] != kid


# ── artifacts ───────────────────────────────────────────────────────

def test_sign_artifact_verifies_and_detects_tamper(env):
    fake = FakeEternitas()
    ak.ensure_registered(transport=fake.transport())
    sec = json.loads(env.read_text())["eternitas"]
    jwk = ak.public_jwk(ak.load_private_key(sec["private_key"]))
    jws = ak.sign_artifact(b"hello drops")
    h, p, _ = jws.split(".")
    assert p == "", "detached"
    v = ak.verify_jws(jws, jwk, detached_payload=b"hello drops")
    assert v["header"]["typ"] == "eternitas-sig+jws"
    assert v["header"]["kid"] == sec["kid"] and v["header"]["passport"] == PASSPORT
    assert v["header"]["signed_at"]
    assert ak.verify_jws(jws, jwk, detached_payload=b"hello dropz") is None
    forged = json.loads(base64.urlsafe_b64decode(h + "=="))
    forged["passport"] = "ET26-EVIL-EVIL"
    h2 = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
    assert ak.verify_jws(jws.replace(h, h2, 1), jwk, detached_payload=b"hello drops") is None


def test_sign_artifact_without_key_raises(env):
    with pytest.raises(RuntimeError):
        ak.sign_artifact(b"x")


# ── custody ─────────────────────────────────────────────────────────

def test_no_key_material_or_tokens_in_logs(env, caplog, monkeypatch, capsys):
    fake = FakeEternitas()
    with caplog.at_level(logging.DEBUG):
        ak.ensure_registered(transport=fake.transport())
        ak.rotate(transport=fake.transport())
        ak.reset(transport=fake.transport(), owner_token=lambda: "FRESH-HUB-TOKEN")
        kid = json.loads(env.read_text())["eternitas"]["kid"]
        ak.revoke(kid, transport=fake.transport())
        from windyfly.commands import agent_key
        real_status = ak.status
        monkeypatch.setattr(ak, "status", lambda: real_status(transport=fake.transport()))
        agent_key.cmd_agent_key(argparse.Namespace(action="status"))
    text = caplog.text + capsys.readouterr().out
    assert "PRIVATE KEY" not in text
    assert EPT not in text and "FRESH-HUB-TOKEN" not in text
    for b in env.parent.glob("credentials.json*"):
        for line in json.loads(b.read_text()).get("eternitas", {}).get("private_key", "").splitlines()[1:-1]:
            assert line not in text


def test_status_never_returns_private_keys(env):
    ak.ensure_registered(transport=FakeEternitas().transport())
    info = ak.status(transport=FakeEternitas().transport())
    assert "PRIVATE KEY" not in json.dumps(info)


def test_cloud_backup_ships_only_the_database():
    from windyfly import cloud_backup
    src = inspect.getsource(cloud_backup)
    assert "credentials" not in src.replace("bot_credentials", "")
    assert "windyfly.db" in src
