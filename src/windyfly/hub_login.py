"""Sign in with your Windy account from the terminal (loopback + PKCE).

Eternitas is closing its anonymous hatch door: ``POST /bots/auto-hatch``
will require a credential. For a terminal hatch (``windy go``) that
credential is the owner's hub login token, the same account.windyword.ai
access token the web and desktop apps use. This module gets one the
standard way for a native app (RFC 8252):

1. Bind a one-shot HTTP listener on ``127.0.0.1`` at a random port.
2. Send the browser to the hub's authorize endpoint with a PKCE S256
   challenge and a random ``state``. The redirect is
   ``http://127.0.0.1:<port>/api/auth/hub/callback``. The hub has the
   public client ``windy-fly-dashboard`` registered with PORTLESS IP-literal
   loopback redirects, so any port matches, and ``localhost`` is refused.
3. Exchange the code (plus the verifier) at the token endpoint.
4. Keep ``{access_token, refresh_token, expires_at, windy_identity_id}``
   in ``<state dir>/hub_session.json``, mode 0600.

Identity is the ``windy_identity_id`` claim. It is never ``sub`` (on
access tokens that's the hub's user id, a different value) and never the
email: the hub issues full tokens to UNVERIFIED emails for 24 hours.

Tokens are never logged or printed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

import httpx

from windyfly.auth.jwt_claims import read_jwt_claims

logger = logging.getLogger(__name__)

DEFAULT_HUB_URL = "https://account.windyword.ai"
DEFAULT_CLIENT_ID = "windy-fly-dashboard"
CALLBACK_PATH = "/api/auth/hub/callback"
LOGIN_TIMEOUT_S = 300
REFRESH_MARGIN_S = 60
_HTTP_TIMEOUT = 20.0


class LoginError(RuntimeError):
    """The sign-in did not complete. The message is safe to show a person."""


# ── config ────────────────────────────────────────────────────────────

def hub_url() -> str:
    return os.environ.get("WINDY_HUB_URL", DEFAULT_HUB_URL).rstrip("/")


def client_id() -> str:
    return os.environ.get("HUB_OAUTH_CLIENT_ID", DEFAULT_CLIENT_ID)


def session_path() -> Path:
    from windyfly.platform import windy_state_dir

    return windy_state_dir() / "hub_session.json"


# ── claims ────────────────────────────────────────────────────────────

def identity_claim(claims: dict[str, Any] | None) -> str:
    """The stable Windy identity from hub claims, or ""; never sub, never email."""
    if not claims:
        return ""
    for key in ("windy_identity_id", "windyIdentityId"):
        val = claims.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _jwt_header(token: str) -> dict[str, Any]:
    try:
        seg = token.split(".")[0]
        seg += "=" * (-len(seg) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(seg.encode("ascii")))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def looks_like_hub_human_token(token: str) -> bool:
    """Shape check only (no signature check; Eternitas verifies).

    RS256 JWT with ``type: human`` and a Windy identity. Keeps an EPT, an
    operator JWT or a stray string from being sent as the owner's login.
    """
    if not token or token.count(".") != 2:
        return False
    if _jwt_header(token).get("alg") != "RS256":
        return False
    claims = read_jwt_claims(token)
    return bool(claims and claims.get("type") == "human" and identity_claim(claims))


# ── PKCE / URLs ───────────────────────────────────────────────────────

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(48))  # 64 chars, within 43..128
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def redirect_uri_for(port: int) -> str:
    return f"http://127.0.0.1:{port}{CALLBACK_PATH}"


def build_authorize_url(redirect_uri: str, state: str, challenge: str) -> str:
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": redirect_uri,
        "scope": "openid profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{hub_url()}/api/v1/oauth/authorize?{query}"


# ── session file ──────────────────────────────────────────────────────

def _write_session(data: dict[str, Any]) -> None:
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(data).encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_session() -> dict[str, Any] | None:
    try:
        data = json.loads(session_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("access_token") else None


def logout() -> bool:
    """Forget the stored sign-in. True if there was one."""
    try:
        session_path().unlink()
        return True
    except FileNotFoundError:
        return False


def _session_from_tokens(tokens: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    access = tokens.get("access_token") or ""
    if not access:
        raise LoginError("The Windy sign-in server returned no access token.")
    claims = read_jwt_claims(access) or {}
    identity = identity_claim(claims)
    if not identity:
        raise LoginError("The Windy sign-in token has no account identity (windy_identity_id).")
    if claims.get("type") not in (None, "human"):
        raise LoginError("That sign-in is not a personal Windy account.")
    now = time.time()
    expires_in = tokens.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        expires_at = now + float(expires_in)
    else:
        expires_at = float(claims.get("exp") or now + 900)
    refresh = tokens.get("refresh_token") or (previous or {}).get("refresh_token") or ""
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": expires_at,
        "windy_identity_id": identity,
        "hub_url": hub_url(),
    }


# ── token endpoint ────────────────────────────────────────────────────

def _token_request(form: dict[str, str], transport: httpx.BaseTransport | None) -> dict[str, Any]:
    with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
        resp = client.post(
            f"{hub_url()}/api/v1/oauth/token",
            data=form,
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200:
        # The body may echo the code; keep it out of logs.
        raise LoginError(f"The Windy sign-in server refused the token request (HTTP {resp.status_code}).")
    try:
        body = resp.json()
    except ValueError as exc:
        raise LoginError("The Windy sign-in server sent an unreadable reply.") from exc
    if not isinstance(body, dict):
        raise LoginError("The Windy sign-in server sent an unreadable reply.")
    return body


def exchange_code(
    code: str, verifier: str, redirect_uri: str,
    *, transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    return _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id(),
        "code_verifier": verifier,
    }, transport)


def get_access_token(*, transport: httpx.BaseTransport | None = None) -> str | None:
    """A currently valid hub access token, refreshing it if needed; else None."""
    session = load_session()
    if not session:
        return None
    if float(session.get("expires_at") or 0) - time.time() > REFRESH_MARGIN_S:
        return session["access_token"]
    refresh = session.get("refresh_token") or ""
    if not refresh:
        return None
    try:
        tokens = _token_request({
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id(),
        }, transport)
        fresh = _session_from_tokens(tokens, previous=session)
    except (LoginError, httpx.HTTPError) as exc:
        logger.info("Windy sign-in refresh failed: %s", exc)
        return None
    _write_session(fresh)
    return fresh["access_token"]


def current_identity() -> str:
    session = load_session()
    return (session or {}).get("windy_identity_id", "") or ""


# ── the loopback login ────────────────────────────────────────────────

class _CallbackServer(http.server.HTTPServer):
    result: dict[str, str] | None = None
    done: threading.Event


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return  # the query string carries the code; never log it

    def _reply(self, status: int, text: str) -> None:
        body = (
            "<!doctype html><meta charset=utf-8><title>Windy Fly</title>"
            "<body style='font-family:system-ui;margin:3rem;text-align:center'>"
            f"<div style='font-size:2.5rem'>🪰</div><p>{text}</p></body>"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != CALLBACK_PATH:
            self._reply(404, "Not found.")
            return
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items() if v}
        if self.server.result is None:
            self.server.result = params
            self.server.done.set()
        if "error" in params:
            self._reply(400, "Sign-in was cancelled. You can close this tab.")
        else:
            self._reply(200, "You're signed in. You can close this tab and go back to the terminal.")


def login(
    open_browser: bool = True,
    *,
    timeout: float = LOGIN_TIMEOUT_S,
    transport: httpx.BaseTransport | None = None,
    on_url: Callable[[str], None] | None = None,
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run the browser sign-in and store the session. Returns {windy_identity_id}.

    ``on_url`` receives the authorize URL (tests use it to play the browser).
    """
    verifier, challenge = new_pkce_pair()
    state = secrets.token_urlsafe(24)

    server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
    server.done = threading.Event()
    port = server.server_address[1]
    redirect_uri = redirect_uri_for(port)
    url = build_authorize_url(redirect_uri, state, challenge)

    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    thread.start()
    try:
        echo("Sign in with your Windy account in your browser:")
        echo(f"  {url}")
        echo(
            f"(On a remote machine over SSH? Forward the port first: "
            f"ssh -L {port}:127.0.0.1:{port} <this host>, then open the link on your computer.)"
        )
        if open_browser:
            try:
                import webbrowser

                webbrowser.open(url, new=2)
            except Exception:
                pass
        if on_url is not None:
            on_url(url)
        if not server.done.wait(timeout):
            raise LoginError(
                f"No sign-in within {int(timeout // 60)} minutes. Run `windy login` to try again."
            )
    finally:
        server.shutdown()
        server.server_close()

    params = server.result or {}
    if params.get("error"):
        raise LoginError("Sign-in was cancelled in the browser.")
    got_state = params.get("state", "")
    if not got_state or not hmac.compare_digest(got_state, state):
        raise LoginError("Sign-in failed a security check (state mismatch). Run `windy login` again.")
    code = params.get("code", "")
    if not code:
        raise LoginError("The browser came back without a sign-in code.")

    session = _session_from_tokens(exchange_code(code, verifier, redirect_uri, transport=transport))
    _write_session(session)
    return {"windy_identity_id": session["windy_identity_id"]}
