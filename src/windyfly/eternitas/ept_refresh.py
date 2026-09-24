"""Keep the agent's Eternitas Passport Token (EPT) fresh.

The EPT used to be written once, at hatch, and never touched again. With a
365-day lifetime that meant a self-hosted agent carried its birth claim set
for up to a year: an agent hatched before Eternitas added
``windy_identity_id`` / ``passport_number`` kept a token without them, so
Windy services couldn't attribute its work to its owner.

Eternitas now has a self-refresh door (Eternitas #159):

    POST {ETERNITAS_URL}/api/v1/bots/{passport}/ept/refresh
    Authorization: Bearer <token>

where ``<token>`` is either
  (a) the bot's OWN current, unexpired EPT (``sub`` == passport), or
  (b) the owner's hub access token (from ``windy login``), or
  (c) no bearer at all but an ``Eternitas-Agent-Proof`` header: a JWS by the
      agent's registered agent-keys v1 key over {passport, nonce, iat, htm,
      htu}. Tried FIRST when the agent has a registered key; (a)/(b) are the
      fallbacks. (b) and (c) are the ways back in once the EPT has expired.

Eternitas decides whether to re-issue (stale claims version, owner link
changed, <30 days left, missing/unparseable); ``reissued: false`` means the
stored token came back unchanged.

``refresh_ept()`` never raises and never logs a token — only the passport
prefix, whether it re-issued, and why.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ENV_KEY = "ETERNITAS_PASSPORT_TOKEN"
PASSPORT_KEY = "ETERNITAS_PASSPORT"
REFRESH_WINDOW_S = 30 * 24 * 3600   # ask Eternitas when <30 days remain
_HTTP_TIMEOUT = 20.0


# ── token helpers (unverified decode: Eternitas verifies) ────────────

def _claims(token: str) -> dict[str, Any] | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _passport_prefix(passport: str) -> str:
    return (passport[:9] + "…") if passport else "?"


def needs_refresh(token: str, now: float | None = None) -> tuple[bool, str]:
    """Cheap local pre-check. Eternitas has the final say."""
    if not token:
        return True, "missing"
    claims = _claims(token)
    if claims is None:
        return True, "unparseable"
    now = time.time() if now is None else now
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return True, "no_exp"
    if exp - now < REFRESH_WINDOW_S:
        return True, "expiring" if exp > now else "expired"
    if "cvr" not in claims:
        return True, "stale_claims"
    return False, "current"


# ── env file ─────────────────────────────────────────────────────────

def resolve_env_file() -> Path | None:
    """Where the EPT is persisted.

    1. ``WINDY_ENV_FILE`` (set this in systemd deployments to the unit's
       ``EnvironmentFile=``), else
    2. the ``.env`` that ``windy go`` writes in the project root.

    Returns None when neither exists and is writable.
    """
    explicit = os.environ.get("WINDY_ENV_FILE", "").strip()
    if explicit:
        p = Path(explicit).expanduser()
    else:
        try:
            from windyfly.platform import get_project_root
            p = get_project_root() / ".env"
        except Exception:
            return None
    if p.is_file() and os.access(p, os.W_OK) and os.access(p.parent, os.W_OK):
        return p
    return None


def _persist(path: Path, token: str) -> bool:
    """Replace ONLY the EPT line, atomically, keeping mode + a backup."""
    try:
        original = path.read_text(encoding="utf-8")
        mode = path.stat().st_mode & 0o777
        lines = original.splitlines()
        out, replaced = [], False
        for line in lines:
            if line.startswith(f"{ENV_KEY}="):
                out.append(f"{ENV_KEY}={token}")
                replaced = True
            else:
                out.append(line)
        if not replaced:
            out.append(f"{ENV_KEY}={token}")
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = path.with_name(f"{path.name}.bak-ept-{stamp}")
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
        return True
    except Exception as exc:
        logger.warning("EPT refresh: could not write %s (%s)", path, type(exc).__name__)
        return False


# ── the refresh ──────────────────────────────────────────────────────

def _token_in_file(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{ENV_KEY}="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


class _FileLock:
    """Exclusive advisory lock next to the env file (POSIX); no-op elsewhere.

    Several processes (one per channel, plus the voice bridge) share one env
    file and all refresh at boot. The lock makes them take turns, and the
    re-read under it lets later ones adopt the token the first one wrote
    instead of asking Eternitas again."""

    def __init__(self, path: Path | None) -> None:
        self._path = path.with_name(f".{path.name}.ept-lock") if path else None
        self._fh = None

    def __enter__(self):
        if self._path is None:
            return self
        try:
            import fcntl
            self._fh = open(self._path, "a", encoding="utf-8")
            fcntl.flock(self._fh, fcntl.LOCK_EX)
        except Exception:
            self._fh = None
        return self

    def __exit__(self, *exc) -> None:
        if self._fh is not None:
            try:
                import fcntl
                fcntl.flock(self._fh, fcntl.LOCK_UN)
            finally:
                self._fh.close()


def refresh_ept(
    force: bool = False,
    *,
    transport: httpx.BaseTransport | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Refresh the EPT if it needs it. Never raises.

    Result ``status`` is one of: ``current`` (nothing to do), ``adopted``
    (another process already renewed it in the env file), ``refreshed``,
    ``unchanged`` (Eternitas returned the same token), ``needs_login``,
    ``no_passport``, ``failed``.
    """
    try:
        env_file = resolve_env_file()
        with _FileLock(env_file):
            if env_file is not None and not force:
                on_disk = _token_in_file(env_file)
                mine = os.environ.get(ENV_KEY, "").strip()
                if on_disk and on_disk != mine and not needs_refresh(on_disk, now)[0]:
                    os.environ[ENV_KEY] = on_disk
                    logger.info("EPT refresh: adopted the token already renewed in %s", env_file)
                    return {"status": "adopted", "env_file": str(env_file)}
            return _refresh(force, transport, now)
    except Exception as exc:  # belt and braces: callers run this at boot
        logger.warning("EPT refresh: unexpected %s", type(exc).__name__)
        return {"status": "failed", "error": type(exc).__name__}


def _refresh_by_agent_key(passport: str, url: str, transport) -> httpx.Response | None:
    """POST /ept/refresh with ``Eternitas-Agent-Proof`` signed by the agent's
    active registered key. None (→ use the bearer paths) when there is no such
    key or the attempt fails; Eternitas never falls through to Bearer itself."""
    try:
        from windyfly.eternitas import agent_keys
        if not agent_keys.has_registered_key():
            return None
        with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
            proof = agent_keys.agent_proof(client, passport, "POST", f"/api/v1/bots/{passport}/ept/refresh")
            if not proof:
                return None
            resp = client.post(url, json={}, headers={"Eternitas-Agent-Proof": proof})
    except Exception as exc:
        logger.info("EPT refresh for %s: agent-key proof unavailable (%s); using the token paths",
                    _passport_prefix(passport), type(exc).__name__)
        return None
    if resp.status_code == 200:
        return resp
    from windyfly.eternitas.agent_keys import error_code
    code, message = error_code(resp)
    logger.warning("EPT refresh for %s via agent key: HTTP %s %s %s; trying the token paths",
                   _passport_prefix(passport), resp.status_code, code, message)
    return None


def _refresh(force: bool, transport, now) -> dict[str, Any]:
    token = os.environ.get(ENV_KEY, "").strip()
    due, why = needs_refresh(token, now)
    if not force and not due:
        return {"status": "current"}

    claims = _claims(token) or {}
    passport = os.environ.get(PASSPORT_KEY, "").strip() or str(claims.get("sub") or "")
    if not passport:
        logger.info("EPT refresh: no passport on this agent (not hatched?)")
        return {"status": "no_passport"}

    from windyfly.eternitas.url import resolve_eternitas_url
    base = resolve_eternitas_url("https://api.eternitas.ai")
    url = f"{base}/api/v1/bots/{passport}/ept/refresh"

    # (c) The agent's own registered signing key (agent-keys v1). Needs no
    # bearer at all, so it also works once the EPT has lapsed. Any failure
    # falls back to (a)/(b) below.
    resp: httpx.Response | None = _refresh_by_agent_key(passport, url, transport)
    path_used = "agent_key" if resp is not None else ""

    if resp is None:
        t = time.time() if now is None else now
        exp = claims.get("exp")
        bearer = ""
        if token and isinstance(exp, (int, float)) and exp > t and claims.get("sub") == passport:
            bearer, path_used = token, "own_ept"
        else:
            try:
                from windyfly import hub_login
                hub = hub_login.get_access_token()
            except Exception:
                hub = None
            if hub:
                bearer, path_used = hub, "owner_hub_login"
        if not bearer:
            logger.warning(
                "EPT refresh for %s: the passport token has expired and there's no "
                "Windy sign-in to renew it — run `windy login`, then `windy ept refresh`.",
                _passport_prefix(passport),
            )
            return {"status": "needs_login", "reason": why}
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
                resp = client.post(url, json={}, headers={"Authorization": f"Bearer {bearer}"})
        except httpx.HTTPError as exc:
            logger.warning("EPT refresh for %s: Eternitas unreachable (%s)",
                           _passport_prefix(passport), type(exc).__name__)
            return {"status": "failed", "error": "unreachable"}

    if resp.status_code != 200:
        from windyfly.eternitas.agent_keys import error_code
        code, detail = error_code(resp)
        logger.warning("EPT refresh for %s via %s: HTTP %s %s %s",
                       _passport_prefix(passport), path_used, resp.status_code, code, detail)
        if resp.status_code in (401, 403) and path_used == "own_ept":
            return {"status": "failed", "http": resp.status_code,
                    "hint": "run `windy login`, then `windy ept refresh --force`"}
        return {"status": "failed", "http": resp.status_code}

    try:
        data = resp.json()
        new_token = str(data.get("ept_token") or "")
    except Exception:
        data, new_token = {}, ""
    if not new_token and data.get("reissued") is False:
        # Eternitas kept the current EPT and said why (reissue_reason); it
        # need not echo the token back. That's success, not a failure.
        logger.info("EPT refresh for %s: kept (%s)", _passport_prefix(passport),
                    data.get("reissue_reason") or data.get("reason"))
        return {"status": "unchanged", "reissued": False,
                "reason": data.get("reissue_reason") or data.get("reason")}
    if not new_token or _claims(new_token) is None:
        logger.warning("EPT refresh for %s: response carried no usable token",
                       _passport_prefix(passport))
        return {"status": "failed", "error": "no_token"}

    reissued = bool(data.get("reissued"))
    reason = data.get("reissue_reason")
    if new_token == token:
        logger.info("EPT refresh for %s: current (reissued=%s)", _passport_prefix(passport), reissued)
        return {"status": "unchanged", "reissued": reissued, "reason": reason}

    os.environ[ENV_KEY] = new_token
    env_file = resolve_env_file()
    persisted = env_file is not None and _persist(env_file, new_token)
    if persisted:
        logger.info("EPT refresh for %s via %s: reissued=%s reason=%s, saved to %s",
                    _passport_prefix(passport), path_used, reissued, reason, env_file)
    else:
        logger.warning(
            "EPT refresh for %s via %s: reissued=%s reason=%s — IN-PROCESS ONLY "
            "(no writable env file; set WINDY_ENV_FILE to the file your service "
            "loads, e.g. the systemd EnvironmentFile), so a restart reverts it.",
            _passport_prefix(passport), path_used, reissued, reason,
        )
    new_claims = _claims(new_token) or {}
    return {
        "status": "refreshed",
        "reissued": reissued,
        "reason": reason,
        "via": path_used,
        "persisted": persisted,
        "env_file": str(env_file) if persisted else None,
        "expires_at": data.get("expires_at"),
        "has_windy_identity_id": "windy_identity_id" in new_claims,
    }


def refresh_in_background() -> threading.Thread | None:
    """Fire-and-forget for boot: never delays or fails startup.

    Skipped when ``WINDY_DISABLE_EPT_REFRESH`` is set, and under pytest, so
    test suites that run the real boot sequence never call Eternitas with
    whatever EPT happens to be in the developer's environment."""
    if os.environ.get("WINDY_DISABLE_EPT_REFRESH") or os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    t = threading.Thread(target=refresh_ept, name="ept-refresh", daemon=True)
    t.start()
    return t
