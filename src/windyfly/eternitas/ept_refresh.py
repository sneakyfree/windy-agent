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

A KEY-BOUND token (Eternitas ept-key-binding.v1, its switch EPT_KEY_BOUND): with that switch on, path (c)
returns a token that names this computer's agent key (claim ``akid``), lives 24 hours, and is stored nowhere at
Eternitas; the reply carries ``expires_in``. Three rules follow here:

- it is renewed at HALF its life (``expires_in`` when the reply said it, else the token's own ``iat``/``exp``), by a
  check on every maintenance tick, not by the once-a-day job a year-long token gets;
- no copy of it is kept: the env file is still replaced atomically, but no ``.bak-ept-`` copy is made of a file
  that holds one, and the older ``.bak-ept-`` copies (each holds a year-long token) are wiped when the first one
  arrives, so this computer keeps nothing that outlives its key;
- a renew refused with ``ept_key_inactive`` is the key stop ONLY when the token names the key this computer uses
  now. After this computer's own rotation the old token names the retired key: no stop, the next ask signs with
  the new key.

``refresh_ept()`` never raises and never logs a token — only the passport
prefix, whether it re-issued, and why.
"""

from __future__ import annotations

import base64
import hashlib
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

REVOKED_REFRESH_HINT = "My passport is suspended or revoked, so I can't renew it. My owner can check it in Windy."

ENV_KEY = "ETERNITAS_PASSPORT_TOKEN"
PASSPORT_KEY = "ETERNITAS_PASSPORT"
REFRESH_WINDOW_S = 30 * 24 * 3600   # ask Eternitas when <30 days remain (a year-long token)
KEY_BOUND_LIFE_S = 24 * 3600        # a key-bound token's life when it carries no iat to measure it by
KEY_INACTIVE = "ept_key_inactive"   # Eternitas: this token's agent key is retired or revoked
# The renew time Eternitas' last reply gave (now + expires_in / 2), for that one token; in this process only. After
# a restart the token's own iat and exp say the same thing.
_renew_at: dict[str, float] = {}
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


def _fp(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()


def is_key_bound(token: str) -> bool:
    """True for a token that names an agent key (``akid``): Eternitas holds it to that key and to a short life."""
    akid = (_claims(token) or {}).get("akid") if token else None
    return isinstance(akid, str) and bool(akid)


def renew_time(token: str) -> float | None:
    """When a short-lived token is to be renewed: half its life. None for a year-long token (the 30-day rule)."""
    told = _renew_at.get(_fp(token)) if token else None
    if told is not None:
        return told
    if not is_key_bound(token):
        return None
    claims = _claims(token) or {}
    exp, iat = claims.get("exp"), claims.get("iat")
    if not isinstance(exp, (int, float)):
        return None
    life = exp - iat if isinstance(iat, (int, float)) and exp > iat else KEY_BOUND_LIFE_S
    return exp - life / 2


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
    half = renew_time(token)
    if half is not None:
        if exp <= now:
            return True, "expired"
        if now >= half:
            return True, "half_life"
    elif exp - now < REFRESH_WINDOW_S:
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


def _drop_ept_copies(path: Path) -> int:
    """Wipe every ``.bak-ept-`` copy of the env file (zeros, fsync, remove). Each holds an older passport token."""
    from windyfly.eternitas.agent_keys import _wipe

    gone = 0
    for b in path.parent.glob(f"{path.name}.bak-ept-*"):
        try:
            if b.is_file() and not b.is_symlink():
                _wipe(b)
                gone += 1
        except OSError as exc:
            logger.warning("EPT refresh: could not remove an old env-file copy (%s)", type(exc).__name__)
    return gone


def _persist(path: Path, token: str) -> bool:
    """Replace ONLY the EPT line, atomically, keeping mode + a backup. No backup when the file holds, or is about
    to hold, a key-bound token: a copy of such a token is exactly what must not stay on a disk; and when one
    arrives, the older copies (year-long tokens) are wiped too. The atomic replace is what protects the file."""
    try:
        original = path.read_text(encoding="utf-8")
        mode = path.stat().st_mode & 0o777
        lines = original.splitlines()
        out, replaced, old = [], False, ""
        for line in lines:
            if line.startswith(f"{ENV_KEY}="):
                old = line.split("=", 1)[1].strip()
                out.append(f"{ENV_KEY}={token}")
                replaced = True
            else:
                out.append(line)
        if not replaced:
            out.append(f"{ENV_KEY}={token}")
        bound = is_key_bound(token)
        if not bound and not is_key_bound(old):
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            backup = path.with_name(f"{path.name}.bak-ept-{stamp}")
            shutil.copy2(path, backup)
            from windyfly.state_files import restrict_to_owner

            restrict_to_owner(backup)  # 0600 whatever the env file's mode: it holds the env file's secrets (Hub)
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
        if bound:
            gone = _drop_ept_copies(path)
            if gone:
                logger.info("EPT refresh: the token is now bound to this computer's key; removed %d older "
                            "env-file copies", gone)
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


def _refresh_by_agent_key(passport: str, url: str, transport,
                          refused: list[httpx.Response] | None = None) -> httpx.Response | None:
    """POST /ept/refresh with ``Eternitas-Agent-Proof`` signed by the agent's
    active registered key. None (→ use the bearer paths) when there is no such
    key or the attempt fails; Eternitas never falls through to Bearer itself.
    When Eternitas ANSWERED and refused the proof (a 4xx other than 429), that answer is appended to ``refused``:
    the caller then never renews with the owner's cached Windy sign-in (Eternitas, BOARD 2026-11-11T08:18:15Z)."""
    try:
        from windyfly.eternitas import agent_keys
        if not agent_keys.has_registered_key():
            return None
        kid = agent_keys.active_kid()
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
    from windyfly.eternitas.agent_keys import _standing_from_refusal, refusal_code
    code, message = refusal_code(resp)
    # Eternitas says why on this call too (eternitas #349: it is the call a moved-away computer makes first). A key
    # it names as revoked or retired is the key_revoked stop; any other refusal changes nothing, as before.
    _standing_from_refusal(resp, code, kid=kid)
    if refused is not None and 400 <= resp.status_code < 500 and resp.status_code != 429:
        refused.append(resp)
    logger.warning("EPT refresh for %s via agent key: HTTP %s %s %s; trying the token paths",
                   _passport_prefix(passport), resp.status_code, code, message)
    return None


def _no_signin_after_key_refusal(passport: str, refused: httpx.Response) -> dict[str, Any]:
    """An agent that holds a key and whose key proof Eternitas refused does not fetch the passport's stored year-long
    token with the owner's cached Windy sign-in: on a computer the agent was moved away from, that sign-in is still
    there, and the fallback would undo the cut (Eternitas, BOARD 2026-11-11T08:18:15Z). If Eternitas named the key
    as ended, the stop is already recorded and says its one sentence; the owner pairs the agent again."""
    from windyfly.eternitas.agent_keys import refusal_code

    code, _ = refusal_code(refused)
    logger.warning("EPT refresh for %s: Eternitas refused this computer's key (HTTP %s %s); the owner's Windy sign-in "
                   "is not used to renew an agent that holds a key", _passport_prefix(passport),
                   refused.status_code, code)
    return {"status": "failed", "http": refused.status_code, "code": code, "via": "agent_key"}


def _owner_hub_token() -> str | None:
    try:
        from windyfly import hub_login
        return hub_login.get_access_token()
    except Exception:
        return None


def _is_before_cutoff(resp: httpx.Response) -> bool:
    from windyfly.eternitas.agent_keys import error_code
    return error_code(resp)[0] == "ept_before_cutoff"


def _key_inactive(resp: httpx.Response, claims: dict[str, Any], passport: str) -> dict[str, Any]:
    """Eternitas refused to renew a key-bound token because its key has ended (401 ept_key_inactive). That is the
    key stop only when the token names the key this computer uses NOW and Eternitas says the key is retired or
    revoked. A token that names a key this computer has since replaced (its own rotation) is just an old token:
    no stop, and the next ask signs with the new key. No sign-in hint either way: signing in does not bring a key
    back."""
    from windyfly.eternitas.agent_keys import KEY_REVOKED, _standing_from_refusal, active_kid, refused_key_status

    akid = claims.get("akid")
    mine = isinstance(akid, str) and bool(akid) and akid == active_kid()
    if mine and refused_key_status(resp) is not None:
        from windyfly.agent import standing

        stop = standing.stopped()
        if not (stop and stop.get("kind") == KEY_REVOKED):   # the key's own refusal may already have said why
            _standing_from_refusal(resp, KEY_REVOKED, kid=akid)
        logger.warning("EPT refresh for %s: Eternitas says this computer's key has ended (%s)",
                       _passport_prefix(passport), refused_key_status(resp))
        return {"status": "failed", "http": 401, "code": KEY_REVOKED}
    logger.info("EPT refresh for %s: the token names a key this computer no longer uses; the next ask signs with "
                "the current key", _passport_prefix(passport))
    return {"status": "failed", "http": 401, "code": KEY_INACTIVE}


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
    key_refused: list[httpx.Response] = []
    resp: httpx.Response | None = _refresh_by_agent_key(passport, url, transport, key_refused)
    path_used = "agent_key" if resp is not None else ""
    after_cutoff = False

    if resp is None:
        t = time.time() if now is None else now
        exp = claims.get("exp")
        bearer = ""
        if token and isinstance(exp, (int, float)) and exp > t and claims.get("sub") == passport:
            bearer, path_used = token, "own_ept"
        elif key_refused:
            return _no_signin_after_key_refusal(passport, key_refused[0])
        else:
            hub = _owner_hub_token()
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
                if resp.status_code == 401 and path_used == "own_ept" and _is_before_cutoff(resp) and key_refused:
                    return _no_signin_after_key_refusal(passport, key_refused[0])
                if resp.status_code == 401 and path_used == "own_ept" and _is_before_cutoff(resp):
                    # Eternitas set an EPT cutoff on this passport (ept-cutoff v1): every EPT issued before it is
                    # dead, this one included. The owner's own Windy sign-in is the normal way back in.
                    hub = _owner_hub_token()
                    logger.warning("EPT refresh for %s: this token is from before the passport's cutoff; %s",
                                   _passport_prefix(passport),
                                   "renewing with the owner's Windy sign-in" if hub else "no Windy sign-in here")
                    if hub:
                        path_used, after_cutoff = "owner_hub_login", True
                        resp = client.post(url, json={}, headers={"Authorization": f"Bearer {hub}"})
        except httpx.HTTPError as exc:
            logger.warning("EPT refresh for %s: Eternitas unreachable (%s)",
                           _passport_prefix(passport), type(exc).__name__)
            return {"status": "failed", "error": "unreachable"}

    if resp.status_code != 200:
        from windyfly.eternitas.agent_keys import _standing_from_refusal, error_code, is_not_born, retry_after_s
        code, detail = error_code(resp)
        if code == KEY_INACTIVE and path_used == "own_ept":
            return _key_inactive(resp, claims, passport)
        _standing_from_refusal(resp, code)
        if is_not_born(code):
            # Mid-canal (birth reservation v1): nothing to renew yet. Wait quietly; never the revoked hint.
            logger.info("EPT refresh for %s: passport still being born; retry later", _passport_prefix(passport))
            return {"status": "not_born_yet", "retry_after": retry_after_s(resp)}
        logger.warning("EPT refresh for %s via %s: HTTP %s %s %s",
                       _passport_prefix(passport), path_used, resp.status_code, code, detail)
        if resp.status_code == 403 and path_used == "own_ept":
            # Eternitas: 403 on the agent's own token = the passport is SUSPENDED or REVOKED. Signing in again cannot fix
            # that, so never suggest it (Hub's pinned line, 10-12).
            return {"status": "failed", "http": 403, "hint": REVOKED_REFRESH_HINT, "code": code}
        if resp.status_code == 401 and (path_used == "own_ept" or after_cutoff):
            # After a cutoff a CACHED owner sign-in from before it is refused too (Eternitas: the cut also ends the owner's
            # older tokens; Hub on #691); a fresh `windy login` mints one after it.
            return {"status": "failed", "http": resp.status_code, "code": code,
                    "hint": "run `windy login`, then `windy ept refresh --force`"}
        return {"status": "failed", "http": resp.status_code, "code": code}

    from windyfly.eternitas.agent_keys import _standing_good_mint
    try:
        data = resp.json()
        new_token = str(data.get("ept_token") or "")
    except Exception:
        data, new_token = {}, ""
    if not isinstance(data, dict):
        data = {}
    # Only a 200 that really carries a token (or Eternitas' explicit "kept") is a good mint that lifts a standing stop;
    # an empty or broken 200 changes nothing (Hub on #709).
    by_key = path_used == "agent_key"   # a token-path renew never lifts a key_revoked stop (agent_keys)
    if not new_token and data.get("reissued") is False:
        _standing_good_mint(by_key=by_key)
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
    # Eternitas minted a usable token for this passport: a standing stop (if any) is lifted
    _standing_good_mint(by_key=by_key)

    reissued = bool(data.get("reissued"))
    reason = data.get("reissue_reason")
    if new_token == token:
        logger.info("EPT refresh for %s: current (reissued=%s)", _passport_prefix(passport), reissued)
        return {"status": "unchanged", "reissued": reissued, "reason": reason}

    expires_in = data.get("expires_in")
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) and expires_in > 0:
        # Eternitas says how long this token lives (a key-bound one: 24 hours). Renew at half of that, by this
        # computer's own clock from now, so a clock that is off against Eternitas' does not move the renew.
        _renew_at.clear()
        _renew_at[_fp(new_token)] = (time.time() if now is None else now) + expires_in / 2
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
        "key_bound": is_key_bound(new_token),
        "has_windy_identity_id": "windy_identity_id" in new_claims,
    }


def renew_short_lived(*, transport: httpx.BaseTransport | None = None, now: float | None = None) -> dict[str, Any] | None:
    """The check a maintenance tick makes (every 15 minutes) for a token that has its own renew time: a key-bound
    token, or one whose reply carried ``expires_in``. Asks Eternitas only once that time has come; None when there
    is nothing to do. A year-long token is not this function's business (the daily job asks for it), and while a
    standing stop is in force the standing probe owns every ask, with its back-off. Never raises."""
    try:
        token = os.environ.get(ENV_KEY, "").strip()
        if renew_time(token) is None or not needs_refresh(token, now)[0]:
            return None
        from windyfly.agent import standing

        if standing.stopped():
            return None
        return refresh_ept(transport=transport, now=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("EPT renew check: unexpected %s", type(exc).__name__)
        return None


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
