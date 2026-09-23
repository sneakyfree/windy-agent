"""``windy deregister`` — the owner permanently revokes their agent's passport.

Contract (Eternitas lane, 2026-09-23; aud confirmed by the hub lane):

1. The owner's hub access token (``windy login`` session). A
   ``windy-fly-dashboard`` first-party token carries ``aud ∋ "eternitas"``.
2. ``POST {ETERNITAS_URL}/api/v1/auth/login-with-windy`` with
   ``{"windy_token": <hub token>}`` → ``TokenResponse``; its ``access_token``
   field is the operator session JWT.
3. ``DELETE {ETERNITAS_URL}/api/v1/bots/{passport}`` with that JWT as Bearer →
   204 = revoked (CRL + passport.revoked fan-out to every platform); 404 = not
   this operator's bot, or unknown.

An UNVERIFIED hub email does not fail at step 2: Eternitas resolves such a token
to a different, unlinked operator, so step 3 would 404. So the email_verified
claim is checked up front and the owner gets a plain instruction instead.

Never raises; returns a status dict. Never logs or returns a token.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from typing import Any

import httpx

from windyfly.eternitas.ept_refresh import ENV_KEY, PASSPORT_KEY, _claims, resolve_env_file

logger = logging.getLogger(__name__)
_HTTP_TIMEOUT = 20.0


def current_passport() -> str:
    """This agent's passport: ETERNITAS_PASSPORT, else the EPT's ``sub``."""
    explicit = os.environ.get(PASSPORT_KEY, "").strip()
    if explicit:
        return explicit
    claims = _claims(os.environ.get(ENV_KEY, "").strip()) or {}
    return str(claims.get("sub") or "")


def _login_with_windy(client: httpx.Client, base: str, hub_token: str) -> tuple[int, str, str]:
    """→ (http status, operator session JWT or "", short detail)."""
    resp = client.post(f"{base}/api/v1/auth/login-with-windy", json={"windy_token": hub_token})
    detail = ""
    try:
        body = resp.json()
        detail = str(body.get("detail") or "")[:120] if isinstance(body, dict) else ""
        token = str(body.get("access_token") or "") if resp.status_code == 200 and isinstance(body, dict) else ""
    except ValueError:
        token = ""
    return resp.status_code, token, detail


def deregister(passport: str, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Revoke ``passport`` at Eternitas as its owner. See the module docstring."""
    from windyfly import hub_login
    from windyfly.eternitas.url import resolve_eternitas_url

    if not passport:
        return {"status": "no_passport"}
    try:
        hub = hub_login.get_access_token(transport=transport)
    except Exception:  # a broken session file must not traceback at the user
        hub = None
    if not hub:
        return {"status": "no_login"}
    if (_claims(hub) or {}).get("email_verified") is False:
        return {"status": "unverified"}

    base = resolve_eternitas_url("https://api.eternitas.ai")
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
            code, op_jwt, detail = _login_with_windy(client, base, hub)
            if code == 401:
                # Maybe an expiry edge at Eternitas: refresh once and retry.
                fresh = hub_login.get_access_token(transport=transport, force_refresh=True)
                if fresh and fresh != hub:
                    code, op_jwt, detail = _login_with_windy(client, base, fresh)
            if code != 200 or not op_jwt:
                return {"status": "login_rejected", "http": code, "detail": detail}
            resp = client.delete(f"{base}/api/v1/bots/{passport}",
                                 headers={"Authorization": f"Bearer {op_jwt}"})
    except httpx.HTTPError as exc:
        logger.warning("deregister: Eternitas unreachable (%s)", type(exc).__name__)
        return {"status": "unreachable"}
    if resp.status_code == 204:
        return {"status": "revoked", "passport": passport}
    if resp.status_code == 404:
        return {"status": "not_found", "passport": passport}
    return {"status": "failed", "http": resp.status_code}


def mark_local_revoked(passport: str) -> dict[str, Any]:
    """Stop presenting the dead token: comment out the EPT line in the env file.

    Touches ONLY the ``ETERNITAS_PASSPORT_TOKEN=`` line of the agent's env file
    (``resolve_env_file``: WINDY_ENV_FILE, else the project-root ``.env``). It
    is rewritten as ``# REVOKED <utc> (windy deregister, <passport>): ETERNITAS_PASSPORT_TOKEN=…``,
    atomically, keeping the file's mode, with a ``.bak-deregister-<utc>`` backup.
    The token is also dropped from this process's environment. Memory, data and
    every other setting are left alone.
    """
    os.environ.pop(ENV_KEY, None)
    path = resolve_env_file()
    if path is None:
        return {"env_file": None, "changed": False}
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    try:
        original = path.read_text(encoding="utf-8")
        mode = path.stat().st_mode & 0o777
        out, changed = [], False
        for line in original.splitlines():
            if line.startswith(f"{ENV_KEY}="):
                out.append(f"# REVOKED {stamp} (windy deregister, {passport}): {line}")
                changed = True
            else:
                out.append(line)
        if not changed:
            return {"env_file": str(path), "changed": False}
        backup = path.with_name(f"{path.name}.bak-deregister-{stamp}")
        shutil.copy2(path, backup)
        os.chmod(backup, mode)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("\n".join(out) + ("\n" if original.endswith("\n") else ""))
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return {"env_file": str(path), "changed": True, "backup": str(backup)}
    except Exception as exc:
        logger.warning("deregister: could not update %s (%s)", path, type(exc).__name__)
        return {"env_file": str(path), "changed": False, "error": type(exc).__name__}
