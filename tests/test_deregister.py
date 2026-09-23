"""`windy deregister`: the owner permanently revokes their agent's passport.

Contract (Eternitas lane): owner hub token → POST /api/v1/auth/login-with-windy
{"windy_token"} → TokenResponse.access_token (operator session) → DELETE
/api/v1/bots/{passport} → 204 revoked / 404 not this operator's bot.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import stat
import time

import httpx
import pytest

from windyfly import hub_login
from windyfly.eternitas import deregister as dr

PASSPORT = "ET26-TEST-DRG1"
HUB_SECRET_TAIL = "hubsecret-DO-NOT-LEAK"
OP_SECRET = "opsession-DO-NOT-LEAK"


def _b64(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def make_jwt(claims: dict) -> str:
    return f"{_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_b64(claims)}.{HUB_SECRET_TAIL}"


def hub_token(**over) -> str:
    claims = {"type": "human", "windy_identity_id": "5e1b9569-aaaa", "email_verified": True,
              "aud": ["windy_fly", "eternitas"], "exp": int(time.time()) + 900}
    claims.update(over)
    return make_jwt(claims)


def ept_for(passport: str) -> str:
    return f"{_b64({'alg': 'ES256'})}.{_b64({'sub': passport, 'exp': int(time.time()) + 86400})}.sig"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")
    for var in ("WINDY_ENV_FILE", dr.ENV_KEY, dr.PASSPORT_KEY, "WINDYFLY_AGENT_NAME"):
        monkeypatch.delenv(var, raising=False)


def _sign_in(token: str | None = None) -> str:
    token = token or hub_token()
    hub_login._write_session({"access_token": token, "refresh_token": "",
                              "expires_at": time.time() + 600, "windy_identity_id": "5e1b9569-aaaa"})
    return token


def _transport(calls: list, *, login_status=200, delete_status=204, login_body=None):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path == "/api/v1/auth/login-with-windy":
            body = login_body if login_body is not None else {
                "access_token": OP_SECRET, "operator_id": "op_27ae", "token_type": "bearer"}
            return httpx.Response(login_status, json=body)
        if req.method == "DELETE" and req.url.path == f"/api/v1/bots/{PASSPORT}":
            return httpx.Response(delete_status)
        return httpx.Response(500)
    return httpx.MockTransport(handler)


# ── the protocol ─────────────────────────────────────────────────────

def test_happy_path_logs_in_with_windy_then_deletes_with_the_operator_session():
    hub = _sign_in()
    calls: list = []
    out = dr.deregister(PASSPORT, transport=_transport(calls))
    assert out == {"status": "revoked", "passport": PASSPORT}
    login, delete = calls
    assert login.method == "POST"
    assert str(login.url) == "https://eternitas.test/api/v1/auth/login-with-windy"
    assert json.loads(login.content) == {"windy_token": hub}
    assert delete.method == "DELETE"
    assert str(delete.url) == f"https://eternitas.test/api/v1/bots/{PASSPORT}"
    assert delete.headers["Authorization"] == f"Bearer {OP_SECRET}"


def test_no_windy_login_session_means_no_network_calls():
    calls: list = []
    assert dr.deregister(PASSPORT, transport=_transport(calls)) == {"status": "no_login"}
    assert calls == []


def test_unverified_email_is_refused_before_calling_eternitas():
    # Eternitas would silently resolve an unverified token to a DIFFERENT
    # operator and the DELETE would 404 — so we stop and say why.
    _sign_in(hub_token(email_verified=False))
    calls: list = []
    assert dr.deregister(PASSPORT, transport=_transport(calls)) == {"status": "unverified"}
    assert calls == []


def test_404_means_not_this_owners_passport():
    _sign_in()
    calls: list = []
    out = dr.deregister(PASSPORT, transport=_transport(calls, delete_status=404))
    assert out == {"status": "not_found", "passport": PASSPORT}


def test_401_from_login_with_windy_refreshes_once_then_retries(monkeypatch):
    _sign_in()
    fresh = hub_token(jti="fresh")
    seen: list = []

    def fake_get(*, transport=None, force_refresh=False):
        seen.append(force_refresh)
        return fresh if force_refresh else hub_token()

    monkeypatch.setattr(hub_login, "get_access_token", fake_get)
    statuses = iter([401, 200])

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("login-with-windy"):
            st = next(statuses)
            return httpx.Response(st, json={"access_token": OP_SECRET, "operator_id": "op"} if st == 200
                                  else {"detail": "Invalid Windy token"})
        return httpx.Response(204)

    out = dr.deregister(PASSPORT, transport=httpx.MockTransport(handler))
    assert out["status"] == "revoked"
    assert seen == [False, True]


def test_login_rejected_twice_reports_without_deleting():
    _sign_in()
    calls: list = []
    out = dr.deregister(PASSPORT, transport=_transport(
        calls, login_status=401, login_body={"detail": "Invalid Windy token"}))
    assert out["status"] == "login_rejected" and out["http"] == 401
    assert all(c.method != "DELETE" for c in calls)


def test_network_error_is_reported_not_raised():
    _sign_in()

    def boom(req):
        raise httpx.ConnectError("down")

    assert dr.deregister(PASSPORT, transport=httpx.MockTransport(boom)) == {"status": "unreachable"}


def test_no_token_appears_in_logs(caplog):
    _sign_in()
    caplog.set_level(logging.DEBUG)
    dr.deregister(PASSPORT, transport=_transport([], delete_status=500))

    def boom(req):
        raise httpx.ConnectError("down")

    dr.deregister(PASSPORT, transport=httpx.MockTransport(boom))
    assert HUB_SECRET_TAIL not in caplog.text and OP_SECRET not in caplog.text


# ── local state ──────────────────────────────────────────────────────

def test_mark_local_revoked_comments_out_only_the_ept_line_atomically(tmp_path, monkeypatch):
    env = tmp_path / "agent.env"
    env.write_text(f"A=1\n{dr.ENV_KEY}=eyJ.dead.token\nB=2\n", encoding="utf-8")
    env.chmod(0o600)
    monkeypatch.setenv("WINDY_ENV_FILE", str(env))
    monkeypatch.setenv(dr.ENV_KEY, "eyJ.dead.token")
    out = dr.mark_local_revoked(PASSPORT)
    assert out["changed"] is True
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "A=1" and lines[2] == "B=2"
    assert lines[1].startswith("# REVOKED ") and PASSPORT in lines[1]
    assert not any(ln.startswith(f"{dr.ENV_KEY}=") for ln in lines)
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    backup = out["backup"]
    assert f"{dr.ENV_KEY}=eyJ.dead.token" in open(backup, encoding="utf-8").read()
    assert dr.ENV_KEY not in __import__("os").environ


def test_mark_local_revoked_without_env_file_only_clears_the_process(monkeypatch):
    monkeypatch.setenv("WINDY_ENV_FILE", "/nonexistent/agent.env")
    monkeypatch.setenv(dr.ENV_KEY, "x")
    assert dr.mark_local_revoked(PASSPORT) == {"env_file": None, "changed": False}


# ── the CLI command ──────────────────────────────────────────────────

def _args(**kw):
    return argparse.Namespace(passport=kw.get("passport"), yes=kw.get("yes", False))


def _capture(monkeypatch):
    from windyfly import cli
    printed: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: printed.append(" ".join(str(x) for x in a)))
    return cli, printed


def test_prompt_defaults_to_no_and_changes_nothing(monkeypatch):
    cli, printed = _capture(monkeypatch)
    monkeypatch.setenv(dr.ENV_KEY, ept_for(PASSPORT))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli.console, "input", lambda *_: "")
    called = []
    monkeypatch.setattr(dr, "deregister", lambda *a, **k: called.append(1) or {})
    cli._cmd_deregister(_args())
    assert called == []
    assert any(PASSPORT in p for p in printed)
    assert any("Cancelled" in p for p in printed)


def test_non_interactive_without_yes_is_refused(monkeypatch):
    cli, printed = _capture(monkeypatch)
    monkeypatch.setenv(dr.ENV_KEY, ept_for(PASSPORT))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    called = []
    monkeypatch.setattr(dr, "deregister", lambda *a, **k: called.append(1) or {})
    cli._cmd_deregister(_args())
    assert called == [] and any("Refusing" in p for p in printed)


def test_yes_revokes_and_marks_local_token(monkeypatch, tmp_path):
    cli, printed = _capture(monkeypatch)
    env = tmp_path / "agent.env"
    token = ept_for(PASSPORT)
    env.write_text(f"{dr.ENV_KEY}={token}\n", encoding="utf-8")
    monkeypatch.setenv("WINDY_ENV_FILE", str(env))
    monkeypatch.setenv(dr.ENV_KEY, token)
    _sign_in()
    monkeypatch.setattr(dr.httpx, "Client", _client_with(_transport([])))
    cli._cmd_deregister(_args(yes=True))
    text = "\n".join(printed)
    assert "revoked" in text and PASSPORT in text
    assert env.read_text(encoding="utf-8").startswith("# REVOKED ")
    assert HUB_SECRET_TAIL not in text and OP_SECRET not in text


def test_no_session_tells_the_user_to_windy_login(monkeypatch):
    cli, printed = _capture(monkeypatch)
    cli._cmd_deregister(_args(passport=PASSPORT, yes=True))
    assert any("windy login" in p for p in printed)


def _client_with(transport):
    real = httpx.Client

    def factory(*a, **k):
        k["transport"] = transport
        return real(*a, **k)
    return factory


def _unset_after_test(monkeypatch, *names):
    # load_dotenv writes os.environ directly; register each var with
    # monkeypatch so whatever the command loads is removed after the test.
    for name in names:
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)


def test_pip_install_finds_its_own_passport_in_the_project_env(monkeypatch, tmp_path):
    """0.7.2 clean-machine proof: `windy deregister --yes` on a pip install said
    "No passport on this agent" because nothing loaded the project .env."""
    cli, printed = _capture(monkeypatch)
    # conftest neutralises load_dotenv suite-wide; this test needs the real one.
    import dotenv
    import dotenv.main
    monkeypatch.setattr(dotenv, "load_dotenv", dotenv.main.load_dotenv)
    _unset_after_test(monkeypatch, dr.ENV_KEY, dr.PASSPORT_KEY, "ETERNITAS_URL")
    monkeypatch.setenv("WINDYFLY_HOME", str(tmp_path))
    token = ept_for(PASSPORT)
    env = tmp_path / ".env"
    env.write_text(f"ETERNITAS_URL=https://eternitas.test\n"
                   f"{dr.PASSPORT_KEY}={PASSPORT}\n{dr.ENV_KEY}={token}\n", encoding="utf-8")
    _sign_in()
    monkeypatch.setattr(dr.httpx, "Client", _client_with(_transport([])))
    cli._cmd_deregister(_args(yes=True))
    text = "\n".join(printed)
    assert "No passport" not in text
    assert f"Passport {PASSPORT} is revoked" in text
    # ...and the local token is marked revoked (it used to stay live in .env)
    lines = env.read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith(f"{dr.ENV_KEY}=") for line in lines)
    assert any(line.startswith("# REVOKED ") and PASSPORT in line for line in lines)
    assert list(tmp_path.glob(".env.bak-deregister-*"))
