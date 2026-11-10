"""Agent signing keys (Eternitas agent-keys v1).

A passport number proves a passport exists, not that the caller holds it. v1
gives every hatched agent its own ES256 (P-256) key pair:

* the PRIVATE key is generated here, on the device the agent runs on, and never
  leaves it — not to Eternitas, not to logs, not to cloud backups (the cloud
  backup ships only the SQLite database);
* Eternitas stores only the PUBLIC JWK, so anyone can verify what the agent
  signs (Windy Drops signed publish, #35) against
  ``GET /api/v1/bots/{passport}/keys``.

Spec: windy-orchestra ``specs/eternitas-agent-keys.v1.md`` (approved
2026-09-23). This module is windy-agent's side (spec rollout step 2).

Storage — ONE file per agent, mode 0600, atomic writes with a timestamped
backup (the same discipline as ``ept_refresh``)::

    WINDY_CREDENTIALS_FILE   explicit path (multi-agent machines, services)
    else  <state dir>/credentials.json   (``WINDY_STATE_DIR`` or ~/.windy)

Schema (other top-level keys in the file are preserved)::

    {"eternitas": {
        "private_key": "<PEM of the ACTIVE key>",   # the Drops SDK reads this
        "kid": "<RFC 7638 thumbprint of the active key>",
        "keys": [{"kid", "private_key", "status": "active|retiring|pending",
                  "created_at", "registered_at"}]}}

At most two keys live in the file: the active one and, during a rotation, the
``retiring`` one until Eternitas confirms the retire.

Nothing here raises into its callers: every public entry point returns a
result dict with a ``status``.
"""

from __future__ import annotations

import base64
import calendar
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

logger = logging.getLogger(__name__)

ALG = "ES256"
CRV = "P-256"
ARTIFACT_TYP = "eternitas-sig+jws"   # spec §3
PLATFORM_POP_TYP = "eternitas-pop+jwt"  # spec §2 mode A
DPOP_TYP = "dpop+jwt"  # RFC 9449
_HTTP_TIMEOUT = 20.0
_UNSUPPORTED_LOGGED = False  # "not yet supported by Eternitas" is logged once per process


# ── base64url + JWK ──────────────────────────────────────────────────

def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def private_key_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def load_private_key(pem: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("not a P-256 private key")
    return key


def public_jwk(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> dict[str, str]:
    """The minimal public JWK (the RFC 7638 required members only)."""
    pub = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    nums = pub.public_numbers()
    return {
        "kty": "EC",
        "crv": CRV,
        "x": _b64u(nums.x.to_bytes(32, "big")),
        "y": _b64u(nums.y.to_bytes(32, "big")),
    }


def thumbprint(jwk: dict[str, Any]) -> str:
    """RFC 7638 JWK thumbprint: SHA-256 over the required members, sorted, no whitespace."""
    if jwk.get("kty") == "EC":
        required = {k: jwk[k] for k in ("crv", "kty", "x", "y")}
    elif jwk.get("kty") == "RSA":  # only for the RFC 7638 test vector
        required = {k: jwk[k] for k in ("e", "kty", "n")}
    else:
        raise ValueError(f"unsupported kty {jwk.get('kty')!r}")
    canonical = json.dumps(required, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _b64u(hashlib.sha256(canonical).digest())


def published_jwk(key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
    """The JWK as registered: required members + kid/alg/use (spec §3 guarantees)."""
    jwk = public_jwk(key)
    return {**jwk, "kid": thumbprint(jwk), "alg": ALG, "use": "sig"}


# ── JWS (compact, ES256) ─────────────────────────────────────────────

def _sign(key: ec.EllipticCurvePrivateKey, signing_input: bytes) -> str:
    der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return _b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _compact(key: ec.EllipticCurvePrivateKey, header: dict[str, Any], payload: bytes,
             *, detached: bool = False) -> str:
    h = _b64u(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64u(payload)
    sig = _sign(key, f"{h}.{p}".encode("ascii"))
    return f"{h}..{sig}" if detached else f"{h}.{p}.{sig}"


def verify_jws(jws: str, jwk: dict[str, Any], detached_payload: bytes | None = None) -> dict[str, Any] | None:
    """Verify a compact (or detached) ES256 JWS against a public JWK.

    Returns ``{"header": …, "payload": bytes}`` or None. Used by the tests and
    by ``windy agent-key status``; real verifiers use Eternitas's SDK."""
    try:
        h, p, s = jws.split(".")
        if detached_payload is not None:
            if p:
                return None
            p = _b64u(detached_payload)
        header = json.loads(_b64u_decode(h))
        if header.get("alg") != ALG:
            return None
        raw = _b64u_decode(s)
        if len(raw) != 64:
            return None
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        pub = ec.EllipticCurvePublicNumbers(
            int.from_bytes(_b64u_decode(jwk["x"]), "big"),
            int.from_bytes(_b64u_decode(jwk["y"]), "big"),
            ec.SECP256R1(),
        ).public_key()
        pub.verify(der, f"{h}.{p}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
        return {"header": header, "payload": _b64u_decode(p)}
    except Exception:
        return None


def registration_proof(key: ec.EllipticCurvePrivateKey, passport: str, nonce: str,
                       iat: int | None = None) -> str:
    """Proof of possession for POST /bots/{p}/keys: a JWS by the NEW key over
    ``{passport, nonce, iat}`` (spec §1). kid AND passport ride in the protected
    header too, matching the artifact rule (§3 decision)."""
    kid = thumbprint(public_jwk(key))
    iat = int(time.time()) if iat is None else iat
    header = {"alg": ALG, "typ": "JWT", "kid": kid, "passport": passport}
    payload = json.dumps({"passport": passport, "nonce": nonce, "iat": iat},
                         separators=(",", ":")).encode("utf-8")
    return _compact(key, header, payload)


def platform_pop(key: ec.EllipticCurvePrivateKey, passport: str, aud: str, nonce: str,
                 iat: int | None = None) -> str:
    """Mode A challenge-response proof for a platform (spec §2 A)."""
    iat = int(time.time()) if iat is None else iat
    header = {"alg": ALG, "typ": PLATFORM_POP_TYP, "kid": thumbprint(public_jwk(key))}
    claims = {"iss": passport, "aud": aud, "nonce": nonce, "iat": iat, "exp": iat + 60}
    return _compact(key, header, json.dumps(claims, separators=(",", ":")).encode("utf-8"))


def dpop_proof(key: ec.EllipticCurvePrivateKey, htm: str, htu: str,
               iat: int | None = None, jti: str | None = None) -> str:
    """RFC 9449 DPoP proof. A helper only; mode B token exchange is Eternitas step 4."""
    iat = int(time.time()) if iat is None else iat
    header = {"typ": DPOP_TYP, "alg": ALG, "jwk": public_jwk(key)}
    claims = {"jti": jti or str(uuid.uuid4()), "htm": htm.upper(), "htu": htu, "iat": iat}
    return _compact(key, header, json.dumps(claims, separators=(",", ":")).encode("utf-8"))


# ── credentials file ─────────────────────────────────────────────────

def credentials_path() -> Path:
    explicit = os.environ.get("WINDY_CREDENTIALS_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    from windyfly.platform import windy_state_dir
    return windy_state_dir() / "credentials.json"


def load_credentials(path: Path | None = None) -> dict[str, Any]:
    path = path or credentials_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_credentials(data: dict[str, Any], path: Path | None = None) -> None:
    """Atomic 0600 write; the previous file (if any) is kept as a timestamped
    0600 backup next to it. Private keys never leave this directory."""
    from windyfly.state_files import ensure_private_dir, restrict_to_owner

    path = path or credentials_path()
    ensure_private_dir(path.parent)
    if path.exists():
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        n = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.bak-{stamp}-{n}")
            n += 1
        shutil.copy2(path, backup)
        restrict_to_owner(backup)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.close(fd)
        restrict_to_owner(Path(tmp))  # before a key is in it (os.fchmod does not exist on Windows)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        restrict_to_owner(path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _section(data: dict[str, Any]) -> dict[str, Any]:
    sec = data.get("eternitas")
    if not isinstance(sec, dict):
        sec = {}
        data["eternitas"] = sec
    if not isinstance(sec.get("keys"), list):
        sec["keys"] = []
    return sec


def _set_active(sec: dict[str, Any], entry: dict[str, Any]) -> None:
    sec["private_key"] = entry["private_key"]
    sec["kid"] = entry["kid"]


def active_entry(data: dict[str, Any]) -> dict[str, Any] | None:
    sec = data.get("eternitas") or {}
    kid = sec.get("kid")
    for e in sec.get("keys") or []:
        if e.get("kid") == kid and e.get("status") == "active":
            return e
    return None


def _new_entry(key: ec.EllipticCurvePrivateKey) -> dict[str, Any]:
    return {
        "kid": thumbprint(public_jwk(key)),
        "private_key": private_key_pem(key),
        "status": "active",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "registered_at": None,
    }


# ── identity + auth ──────────────────────────────────────────────────

def _claims(token: str) -> dict[str, Any]:
    try:
        return json.loads(_b64u_decode(token.split(".")[1]))
    except Exception:
        return {}


def current_passport() -> str:
    env = os.environ.get("ETERNITAS_PASSPORT", "").strip()
    if env:
        return env
    return str(_claims(os.environ.get("ETERNITAS_PASSPORT_TOKEN", "")).get("sub") or "")


def _own_ept(passport: str) -> str:
    token = os.environ.get("ETERNITAS_PASSPORT_TOKEN", "").strip()
    c = _claims(token)
    exp = c.get("exp")
    if token and c.get("sub") == passport and isinstance(exp, (int, float)) and exp > time.time():
        return token
    return ""


def _base_url() -> str:
    from windyfly.eternitas.url import resolve_eternitas_url
    return resolve_eternitas_url("https://api.eternitas.ai")


def stored_hub_token() -> str:
    """The owner's saved hub session token (``windy login``), refreshed if due.

    Good enough for ``revoke`` (spec: owner via hub login). NOT for owner key
    registration: that needs a fresh credential entry, see ``fresh_owner_token``."""
    try:
        from windyfly import hub_login
        return hub_login.get_access_token() or ""
    except Exception:
        return ""


OWNER_REAUTH_MAX_AGE_S = 600  # Eternitas: auth_time within 10 minutes


def fresh_owner_token(*, open_browser: bool = True, echo: Any = print) -> str:
    """A brand-new hub token from a browser sign-in with ``prompt=login``.

    Owner recovery registration (``reset``) requires ``email_verified`` and an
    ``auth_time`` under 10 minutes old. A stored or refreshed token never
    qualifies (refresh omits auth_time), so this always runs a new PKCE login
    and never reads or overwrites the saved session."""
    from windyfly import hub_login
    who = hub_login.login(open_browser=open_browser, echo=echo, reauth=True, store=False)
    token = str(who.get("access_token") or "")
    c = _claims(token)
    auth_time = c.get("auth_time")
    if c.get("email_verified") is not True:
        logger.warning("agent keys: the Windy sign-in says this email isn't verified; "
                       "Eternitas will refuse the reset")
    if not isinstance(auth_time, (int, float)) or time.time() - auth_time > OWNER_REAUTH_MAX_AGE_S:
        logger.warning("agent keys: the Windy sign-in carried no fresh auth_time; "
                       "Eternitas may refuse the reset")
    return token


# ── Eternitas calls ──────────────────────────────────────────────────

class _Unsupported(Exception):
    """The agent-keys routes aren't deployed on this Eternitas yet."""


def _is_route_missing(resp: httpx.Response) -> bool:
    # FastAPI's catch-all 404 says exactly "Not Found"; an unknown passport on a
    # deployed route comes back with a specific detail (spec §3: 404, never 200).
    if resp.status_code != 404:
        return False
    try:
        return resp.json().get("detail") == "Not Found"
    except Exception:
        return True


def _post(client: httpx.Client, path: str, bearer: str | None,
          body: dict[str, Any] | None = None, *, proof: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    if proof:
        headers["Eternitas-Agent-Proof"] = proof
    resp = client.post(f"{_base_url()}{path}", json=body or {}, headers=headers)
    if _is_route_missing(resp):
        raise _Unsupported(path)
    return resp


def error_code(resp: httpx.Response) -> tuple[str, str]:
    """Eternitas errors are ``detail: {code, message}``; older routes send a
    plain string. Returns (code, message); code is "" for the plain form."""
    try:
        detail = resp.json().get("detail", "")
    except Exception:
        return "", ""
    if isinstance(detail, dict):
        return str(detail.get("code") or ""), str(detail.get("message") or "")[:200]
    return "", str(detail)[:200]


class _ChallengeFailed(Exception):
    def __init__(self, resp: httpx.Response) -> None:
        super().__init__(resp.status_code)
        self.resp = resp


def challenge(client: httpx.Client, passport: str) -> str:
    """A single-use nonce (5-min TTL). No auth: the nonce proves nothing alone."""
    ch = _post(client, f"/api/v1/bots/{passport}/keys/challenge", None)
    if ch.status_code not in (200, 201):
        raise _ChallengeFailed(ch)
    nonce = str(ch.json().get("nonce") or "")
    if not nonce:
        raise ValueError("challenge returned no nonce")
    return nonce


def _register(client: httpx.Client, passport: str, key: ec.EllipticCurvePrivateKey, bearer: str,
              *, reason: str | None = None, signer: dict[str, Any] | None = None) -> httpx.Response:
    """Register ``key``. With ``signer`` (key-ops v1, eternitas #302) the OLD active key authorises it with an
    ``Eternitas-Agent-Proof`` header and no bearer is sent; the body proof is the NEW key's own, over a SECOND
    challenge (each nonce is single use)."""
    try:
        nonce = challenge(client, passport)
    except _ChallengeFailed as exc:
        return exc.resp
    body: dict[str, Any] = {
        "jwk": public_jwk(key),  # {kty, crv, x, y}; Eternitas derives the kid
        "custody": "agent",
        "proof": registration_proof(key, passport, nonce),
    }
    if reason:
        body["reason"] = reason  # owner path only: "recovery" | "handover"
    path = f"/api/v1/bots/{passport}/keys"
    if signer is not None:
        try:
            header_proof = _proof_by(client, passport, signer, "POST", path)
        except _ChallengeFailed as exc:
            return exc.resp
        resp = _post(client, path, None, body, proof=header_proof)
    else:
        resp = _post(client, path, bearer, body)
    if resp.status_code in (200, 201):
        got = str(resp.json().get("kid") or "")
        if got != thumbprint(public_jwk(key)):
            # Never trust a kid we can't reproduce; treat as a failed register.
            logger.warning("agent keys: Eternitas returned kid %s…, expected our thumbprint", got[:8])
            return httpx.Response(502, json={"detail": {"code": "kid_mismatch",
                                                        "message": "server kid != RFC 7638 thumbprint"}})
    return resp


def agent_proof(client: httpx.Client, passport: str, htm: str, path: str,
                *, cred_path: Path | None = None) -> str | None:
    """``Eternitas-Agent-Proof`` for a request by this agent's ACTIVE registered
    key: a JWS (header kid) over {passport, nonce, iat, htm, htu}, the nonce
    fresh from /keys/challenge. None when there is no registered key."""
    entry = active_entry(load_credentials(cred_path or credentials_path()))
    if entry is None or not entry.get("registered_at"):
        return None
    return _proof_by(client, passport, entry, htm, path)


def _proof_by(client: httpx.Client, passport: str, entry: dict[str, Any], htm: str, path: str) -> str:
    """An ``Eternitas-Agent-Proof`` signed by THIS key entry (header kid = its kid), over a fresh nonce. ``htu`` is
    the route PATH (Eternitas compares the path only, key-ops v1)."""
    key = load_private_key(entry["private_key"])
    nonce = challenge(client, passport)
    header = {"alg": ALG, "typ": "JWT", "kid": entry["kid"]}
    claims = {"passport": passport, "nonce": nonce, "iat": int(time.time()),
              "htm": htm.upper(), "htu": path}
    return _compact(key, header, json.dumps(claims, separators=(",", ":")).encode("utf-8"))


def key_ops_enabled() -> bool:
    """Key-ops v1 (eternitas #302, live Mon 11-09): the agent rotates with its OWN active key, no passport token.
    DARK until its own GO: ``WINDY_AGENT_KEY_OPS=1``."""
    return os.environ.get("WINDY_AGENT_KEY_OPS", "").strip() == "1"


def has_registered_key(path: Path | None = None) -> bool:
    entry = active_entry(load_credentials(path or credentials_path()))
    return bool(entry and entry.get("registered_at"))


def _note_unsupported() -> dict[str, Any]:
    global _UNSUPPORTED_LOGGED
    if not _UNSUPPORTED_LOGGED:
        logger.info("agent keys not yet supported by Eternitas; will retry later")
        _UNSUPPORTED_LOGGED = True
    else:
        logger.debug("agent keys not yet supported by Eternitas")
    return {"status": "unsupported"}


def _http_fail(what: str, resp: httpx.Response) -> dict[str, Any]:
    code, message = error_code(resp)
    logger.warning("agent keys: %s refused (HTTP %s %s %s)", what, resp.status_code, code, message)
    out: dict[str, Any] = {"status": "failed", "http": resp.status_code, "code": code, "detail": message}
    if code == "too_many_active_keys":
        out["hint"] = "Eternitas already holds 2 active keys for this passport; run `windy agent-key reset`"
    return out


# ── public operations ────────────────────────────────────────────────

_LOCK = threading.Lock()


def ensure_registered(*, transport: httpx.BaseTransport | None = None,
                      path: Path | None = None) -> dict[str, Any]:
    """Make sure this agent has an active key registered at Eternitas.

    Generates a key if there is none (persisted BEFORE it's registered, so a
    failed registration never loses it), then registers it with the agent's
    own EPT (never the owner's session: that's ``reset``). Also finishes a pending
    rotation (retires a ``retiring`` key). Never raises.

    ``status``: registered | already_registered | unsupported | no_passport |
    needs_login | failed.
    """
    try:
        with _LOCK:
            out = _ensure(transport, path or credentials_path())
            prune_key_backups(transport=transport, path=path or credentials_path())
            prune_env_backups()
            return out
    except _Unsupported:
        return _note_unsupported()
    except Exception as exc:
        logger.warning("agent keys: unexpected %s", type(exc).__name__)
        return {"status": "failed", "error": type(exc).__name__}


# ── old credentials backups (Hub on #714; rule OK'd by Hub 11-04 ~22:0xZ) ──────────────────────────────────
#
# save_credentials keeps a 0600 credentials.json.bak-<utc> on every save, so a retired private key lives on in old
# backups for ever. Delete one ONLY when it is older than _BAK_KEEP_DAYS (by the stamp in its name) AND every kid in it
# is in the current file or listed as RETIRED or REVOKED on the passport's live public key list. Any other status
# (active, a status Eternitas may add later such as pending) or a kid the list doesn't show keeps the backup: it fails
# safe (Hub on #725). An unreadable list deletes nothing that needs it; the newest backup is always kept; an
# unparseable one is kept and counted. Deleting = overwrite with zeros + fsync, then unlink: best effort on
# copy-on-write filesystems and SSDs, defence in depth, NOT a secure erase. Logs carry counts only, never a kid. (The env-file copies .bak-ept-/.bak-deregister-
# have their own names and are not touched here.)
_BAK_KEEP_DAYS = 30
_GONE_STATUSES = ("retired", "revoked")


def _bak_stamp(p: Path, base: str, kind: str = "") -> float | None:
    rest = p.name[len(f"{base}.bak-{kind}"):]
    try:
        return calendar.timegm(time.strptime(rest[:16], "%Y%m%dT%H%M%SZ"))
    except ValueError:
        return None


def _kids_in(data: dict[str, Any]) -> set[str] | None:
    sec = data.get("eternitas")
    if not isinstance(sec, dict):
        return None
    kids = {str(e.get("kid")) for e in sec.get("keys") or [] if isinstance(e, dict) and e.get("kid")}
    if sec.get("kid"):
        kids.add(str(sec["kid"]))
    return kids


def _wipe(p: Path) -> None:
    size = p.stat().st_size
    with open(p, "r+b") as fh:
        fh.write(b"\0" * size)
        fh.flush()
        os.fsync(fh.fileno())
    p.unlink()


def prune_key_backups(*, transport: httpx.BaseTransport | None = None, path: Path | None = None,
                      now: float | None = None) -> dict[str, int]:
    """Delete old credentials backups whose keys are all current or retired/revoked (rule above). Never raises."""
    counts = {"deleted": 0, "kept_recent": 0, "kept_active_key": 0, "kept_unreadable": 0, "kept_list_unknown": 0}
    try:
        path = path or credentials_path()
        now = time.time() if now is None else now
        dated = [(st, b) for b in path.parent.glob(f"{path.name}.bak-*")
                 if (st := _bak_stamp(b, path.name)) is not None]
        dated.sort()
        if dated:
            dated.pop()  # the newest is always kept
            counts["kept_recent"] += 1
        current = _kids_in(load_credentials(path)) or set()
        old: list[tuple[Path, set[str]]] = []
        for stamp, b in dated:
            if now - stamp < _BAK_KEEP_DAYS * 86400:
                counts["kept_recent"] += 1
                continue
            try:
                kids = _kids_in(json.loads(b.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                kids = None
            if kids is None:
                counts["kept_unreadable"] += 1
                continue
            old.append((b, kids - current))
        gone: set[str] | None = set()  # kids the live list shows retired or revoked; only these may be wiped
        if any(rest for _, rest in old):
            gone = None
            passport = current_passport()
            if passport:
                try:
                    with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
                        listed = client.get(f"{_base_url()}/api/v1/bots/{passport}/keys")
                    if listed.status_code == 200:
                        gone = {str(k.get("kid")) for k in listed.json().get("keys", [])
                                if k.get("status") in _GONE_STATUSES}
                except Exception:  # noqa: BLE001
                    gone = None
        for b, rest in old:
            if rest and gone is None:
                counts["kept_list_unknown"] += 1
            elif rest - (gone or set()):
                counts["kept_active_key"] += 1
            else:
                _wipe(b)
                counts["deleted"] += 1
        if counts["deleted"] or counts["kept_unreadable"] or counts["kept_list_unknown"]:
            logger.info("agent keys: old credentials backups: deleted %d, kept %d recent, %d with a key not "
                        "retired/revoked, %d unreadable, %d (key list unreadable)", counts["deleted"], counts["kept_recent"],
                        counts["kept_active_key"], counts["kept_unreadable"], counts["kept_list_unknown"])
    except Exception as exc:  # noqa: BLE001  (never break ensure_registered)
        logger.warning("agent keys: backup prune skipped (%s)", type(exc).__name__)
    return counts


def prune_env_backups(env_path: Path | None = None, *, now: float | None = None) -> dict[str, int]:
    """The env-file copies ept_refresh (.bak-ept-) and deregister (.bak-deregister-) make hold that file's secrets
    (an old EPT, API keys). Each is made owner-only (0600); per kind the newest is always kept, and one older than
    _BAK_KEEP_DAYS is wiped (zeros + fsync) and removed (Hub 11-04). Counts only in logs. Never raises."""
    counts = {"deleted": 0, "kept": 0}
    try:
        from windyfly.eternitas.ept_refresh import resolve_env_file
        from windyfly.state_files import restrict_to_owner

        env_path = env_path or resolve_env_file()
        if env_path is None:
            return counts
        now = time.time() if now is None else now
        for kind in ("ept-", "deregister-"):
            dated = []
            for b in env_path.parent.glob(f"{env_path.name}.bak-{kind}*"):
                restrict_to_owner(b)
                st = _bak_stamp(b, env_path.name, kind)
                if st is None:
                    counts["kept"] += 1
                else:
                    dated.append((st, b))
            dated.sort()
            if dated:
                dated.pop()  # the newest of each kind is always kept
                counts["kept"] += 1
            for st, b in dated:
                if now - st < _BAK_KEEP_DAYS * 86400:
                    counts["kept"] += 1
                else:
                    _wipe(b)
                    counts["deleted"] += 1
        if counts["deleted"]:
            logger.info("agent keys: old env-file backups: deleted %d, kept %d", counts["deleted"], counts["kept"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent keys: env-file backup prune skipped (%s)", type(exc).__name__)
    return counts


def _bearer(passport: str) -> tuple[str, str]:
    """Bearer for the agent's own key operations: its current EPT. The owner's
    stored hub session can't register keys (Eternitas wants a fresh sign-in),
    so there is no silent owner fallback; that's what ``reset`` is for."""
    own = _own_ept(passport)
    return (own, "own_ept") if own else ("", "")


def local_kid(*, path: Path | None = None) -> str:
    """This computer's active key id, generating (and saving, unregistered) a key if there is none."""
    return begin_local_key(path=path)[0]


def begin_local_key(*, path: Path | None = None) -> tuple[str, Callable[[], None]]:
    """This computer's active key id for a pairing round, plus an UNDO that removes exactly what this call made.

    Pairing sends the kid BEFORE a passport exists here (Windy Connect binds the code to it). A round that ends
    without the key registered tells the person "Nothing was changed" (Boss 11-03), so the undo puts the credentials
    file back as it was: removed if this call created it, else its exact previous bytes, and the backup this call
    wrote is deleted (it would hold nothing new, but it is still something left behind). A key that existed before,
    or one that got registered meanwhile, is never touched. The private key never leaves this machine."""
    path = path or credentials_path()
    with _LOCK:
        data = load_credentials(path)
        entry = active_entry(data)
        if entry is not None:
            return str(entry["kid"]), lambda: None
        dir_existed = path.parent.exists()
        before = path.read_bytes() if path.exists() else None
        old_backups = set(path.parent.glob(f"{path.name}.bak-*")) if dir_existed else set()
        sec = _section(data)
        entry = _new_entry(generate_private_key())
        sec["keys"] = [e for e in sec["keys"] if e.get("status") == "retiring"] + [entry]
        _set_active(sec, entry)
        save_credentials(data, path)
        logger.info("agent keys: generated a new signing key %s…", entry["kid"][:8])
        kid = str(entry["kid"])

    def undo() -> None:
        with _LOCK:
            now_entry = active_entry(load_credentials(path))
            if now_entry is None or now_entry.get("kid") != kid or now_entry.get("registered_at"):
                return
            try:
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    from windyfly.state_files import restrict_to_owner

                    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
                    restrict_to_owner(Path(tmp))  # owner-only before the old contents go in (#697)
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(before)
                    os.replace(tmp, path)
                for extra in set(path.parent.glob(f"{path.name}.bak-*")) - old_backups:
                    extra.unlink(missing_ok=True)
                if not dir_existed:
                    path.parent.rmdir()  # only when empty; a non-empty folder raises and stays
            except OSError as exc:
                logger.warning("agent keys: could not undo the new key (%s)", type(exc).__name__)
            else:
                logger.info("agent keys: removed the unregistered key %s… this round made", kid[:8])

    return kid, undo


def register_with_bootstrap(passport: str, bootstrap_token: str, *, transport: httpx.BaseTransport | None = None,
                            path: Path | None = None) -> dict[str, Any]:
    """Register this computer's active key for ``passport`` with the one-time bootstrap token a home pairing
    delivered (Windy Connect #105, Hub's desktop door): the owner's token never reaches the computer. The token is
    only ever sent as the Bearer of this one request, never logged or stored. Never raises.

    A key already registered counts only for the SAME passport (the entry records it; an older entry falls back to
    this agent's passport): a key that belongs to another agent is never handed to a new one (Hub on #700). A 409
    key_exists (Eternitas kept an earlier registration whose answer was lost) is settled by reading the passport's
    keys: our kid active there = registered.

    ``status``: registered | already_registered | failed (with http/code)."""
    path = path or credentials_path()
    try:
        with _LOCK:
            data = load_credentials(path)
            entry = active_entry(data)
            if entry is None:
                return {"status": "failed", "code": "no_local_key"}
            if entry.get("registered_at"):
                if (entry.get("passport") or current_passport()) == passport:
                    return {"status": "already_registered", "kid": entry["kid"]}
                return {"status": "failed", "code": "key_belongs_to_other_agent"}
            with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
                resp = _register(client, passport, load_private_key(entry["private_key"]), bootstrap_token)
                if resp.status_code == 409 and error_code(resp)[0] == "key_exists" and \
                        _kid_active_for(client, passport, str(entry["kid"])):
                    resp = httpx.Response(201, json={"kid": entry["kid"]})
                    logger.info("agent keys: %s… was already registered for %s (the earlier answer was lost)",
                                entry["kid"][:8], passport[:9])
            if resp.status_code not in (200, 201):
                return _http_fail("key registration (bootstrap)", resp)
            entry["registered_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            entry["passport"] = passport
            save_credentials(data, path)
            logger.info("agent keys: registered %s… for %s (via bootstrap)", entry["kid"][:8], passport[:9])
            return {"status": "registered", "kid": entry["kid"]}
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:  # never reached Eternitas: nothing was registered
        logger.warning("agent keys: bootstrap registration could not connect (%s)", type(exc).__name__)
        return {"status": "failed", "code": type(exc).__name__}
    except httpx.TransportError as exc:
        # The request may have arrived and the answer got lost: Eternitas may hold this key now. "unknown" keeps the
        # key, so the next round's 409 key_exists settles it (Hub on #700).
        logger.warning("agent keys: bootstrap registration outcome unknown (%s)", type(exc).__name__)
        return {"status": "unknown", "code": type(exc).__name__}
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent keys: bootstrap registration hit %s", type(exc).__name__)
        return {"status": "failed", "code": type(exc).__name__}


def _kid_active_for(client: httpx.Client, passport: str, kid: str) -> bool:
    try:
        listed = client.get(f"{_base_url()}/api/v1/bots/{passport}/keys")
        return listed.status_code == 200 and any(
            k.get("kid") == kid and k.get("status") in ("active", None) for k in listed.json().get("keys", []))
    except Exception:  # noqa: BLE001
        return False


def _kid_still_active(client: httpx.Client, passport: str, kid: str) -> bool | None:
    """True/False from the passport's public key list; None when the list can't be read (then nothing is decided)."""
    try:
        listed = client.get(f"{_base_url()}/api/v1/bots/{passport}/keys")
        if listed.status_code != 200:
            return None
        return any(k.get("kid") == kid and k.get("status") in ("active", None) for k in listed.json().get("keys", []))
    except Exception:  # noqa: BLE001
        return None


def _ensure(transport, path: Path) -> dict[str, Any]:
    passport = current_passport()
    if not passport:
        return {"status": "no_passport"}
    data = load_credentials(path)
    sec = _section(data)
    entry = active_entry(data)
    if entry is None:
        key = generate_private_key()
        entry = _new_entry(key)
        sec["keys"] = [e for e in sec["keys"] if e.get("status") == "retiring"] + [entry]
        _set_active(sec, entry)
        save_credentials(data, path)
        logger.info("agent keys: generated a new signing key %s…", entry["kid"][:8])

    if entry.get("registered_at") and not any(e.get("status") == "retiring" for e in sec["keys"]):
        return {"status": "already_registered", "kid": entry["kid"]}

    bearer, via = _bearer(passport)
    if key_ops_enabled() and entry.get("registered_at"):
        # Only a pending retire is left: each retiring key retires ITSELF with its own proof (no token needed).
        with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
            _retire_pending(client, passport, "", data, path, self_proof=True)
        return {"status": "registered", "kid": entry["kid"], "via": "agent_key", "registered_via": None}
    if not bearer:
        logger.warning("agent keys: can't register (the passport token is missing or expired); "
                       "run `windy login` then `windy ept refresh`, or `windy agent-key reset`")
        return {"status": "needs_login", "kid": entry["kid"]}

    registered_via = None
    with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
        if not entry.get("registered_at"):
            resp = _register(client, passport, load_private_key(entry["private_key"]), bearer)
            if resp.status_code not in (200, 201):
                return _http_fail("key registration", resp)
            registered_via = resp.json().get("registered_via")
            entry["registered_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            entry["passport"] = passport  # whose key this is (Hub on #700)
            save_credentials(data, path)
            logger.info("agent keys: registered %s… for %s (via %s)", entry["kid"][:8], passport[:9], via)
        _retire_pending(client, passport, bearer, data, path)
    return {"status": "registered", "kid": entry["kid"], "via": via, "registered_via": registered_via}


def _retire_pending(client: httpx.Client, passport: str, bearer: str,
                    data: dict[str, Any], path: Path, *, self_proof: bool = False) -> None:
    """Retire every ``retiring`` key. ``self_proof`` (key-ops v1): the key signs its OWN retire (a key may only
    remove itself on the agent-key path; 403 not_this_key otherwise), so no bearer is sent."""
    sec = _section(data)
    for e in [e for e in sec["keys"] if e.get("status") == "retiring"]:
        route = f"/api/v1/bots/{passport}/keys/{e['kid']}/retire"
        if self_proof:
            try:
                resp = _post(client, route, None, proof=_proof_by(client, passport, e, "POST", route))
            except _ChallengeFailed as exc:
                resp = exc.resp
        else:
            resp = _post(client, route, bearer)
        done = resp.status_code in (200, 204, 404, 409)  # 404/409: already gone/retired
        if self_proof and resp.status_code == 409 and error_code(resp)[0] not in ("key_not_active", "already_revoked"):
            done = False  # e.g. 409 last_active_key: Eternitas REFUSED, the key is still active there
        if self_proof and resp.status_code in (400, 401):
            # A key that is no longer active fails the proof check FIRST (401 invalid_agent_proof, or the
            # challenge), so "already retired" never comes back as 409 here (Hub on #714). Read the public list:
            # not active there = done (the retire landed and its answer was lost); still active = keep it.
            done = _kid_still_active(client, passport, str(e["kid"])) is False
        if done:
            sec["keys"] = [k for k in sec["keys"] if k.get("kid") != e["kid"]]
            save_credentials(data, path)
            logger.info("agent keys: retired %s…", e["kid"][:8])
        else:
            _http_fail("key retire", resp)


def rotate(*, transport: httpx.BaseTransport | None = None, path: Path | None = None) -> dict[str, Any]:
    """Register a fresh key, make it active, then retire the old one.

    If the new key can't be registered it's dropped and the old key stays
    active. If the retire fails, the old key stays in the file as ``retiring``
    and the daily job retries."""
    path = path or credentials_path()
    try:
        with _LOCK:
            passport = current_passport()
            if not passport:
                return {"status": "no_passport"}
            data = load_credentials(path)
            sec = _section(data)
            old = active_entry(data)
            if old is None or not old.get("registered_at"):
                return {"status": "failed", "error": "no_registered_key"}
            if key_ops_enabled():  # key-ops v1: the old key authorises, no passport token at all
                return _rotate_by_key(transport, path, passport, data, old)
            bearer, via = _bearer(passport)
            if not bearer:
                return {"status": "needs_login"}
            key = generate_private_key()
            new = _new_entry(key)
            with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
                resp = _register(client, passport, key, bearer)
                if resp.status_code not in (200, 201):
                    return _http_fail("new key registration", resp)
                new["registered_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                new["passport"] = passport
                old["status"] = "retiring"
                sec["keys"] = [old, new]
                _set_active(sec, new)
                save_credentials(data, path)
                _retire_pending(client, passport, bearer, data, path)
            return {"status": "rotated", "kid": new["kid"], "old_kid": old["kid"], "via": via}
    except _Unsupported:
        return _note_unsupported()
    except Exception as exc:
        logger.warning("agent keys: rotate failed (%s)", type(exc).__name__)
        return {"status": "failed", "error": type(exc).__name__}


def _rotate_by_key(transport: httpx.BaseTransport | None, path: Path, passport: str,
                   data: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    """Key-ops v1 rotation (caller holds _LOCK). The NEW key is saved as ``pending`` BEFORE it is sent, so a lost
    answer never loses a key Eternitas may now hold (Hub on #714: an orphan active key nobody holds could not be
    retired on this path, since a key only retires itself). A later rotate reuses that pending key and settles it by
    reading the passport's public key list (our kid active there = registered), as register_with_bootstrap does.

    ``status``: rotated | unknown (the answer was lost; the pending key stays for the next try) | failed."""
    sec = _section(data)
    new = next((e for e in sec["keys"] if e.get("status") == "pending"), None)
    if new is None:
        new = {**_new_entry(generate_private_key()), "status": "pending"}
        sec["keys"].append(new)
        save_credentials(data, path)
    key = load_private_key(new["private_key"])
    with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
        try:
            landed: bool | None = _kid_active_for(client, passport, str(new["kid"]))
            if not landed:
                resp = _register(client, passport, key, "", signer=old)
                code = resp.status_code
                if code in (200, 201):
                    landed = True
                elif code == 409 and error_code(resp)[0] == "key_exists":
                    # Eternitas has this kid: settle from the public list. Unreadable = unknown, never a refusal.
                    landed = _kid_still_active(client, passport, str(new["kid"]))
                elif code >= 500 or code in (408, 409, 429):
                    landed = None  # a proxy 502/504 can come after Eternitas committed (Hub on #714)
                else:
                    landed = False
                if landed is None:
                    if code == 409 and error_code(resp)[0] != "key_exists":  # e.g. too_many_active_keys: refused now, but an earlier lost try may be one of
                        return {**_http_fail("new key registration", resp), "kid": new["kid"]}  # them: keep it
                    logger.warning("agent keys: new key registration outcome unknown (HTTP %s); kept as pending", code)
                    return {"status": "unknown", "http": code, "code": error_code(resp)[0], "kid": new["kid"]}
                if not landed:
                    # A definite 4xx refusal: Eternitas did not take this key. Drop it so nothing piles up.
                    sec["keys"] = [e for e in sec["keys"] if e.get("kid") != new["kid"]]
                    save_credentials(data, path)
                    return _http_fail("new key registration", resp)
        except httpx.TransportError as exc:
            logger.warning("agent keys: new key registration outcome unknown (%s); kept as pending",
                           type(exc).__name__)
            return {"status": "unknown", "code": type(exc).__name__, "kid": new["kid"]}
        new["status"] = "active"
        new["registered_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        new["passport"] = passport
        old["status"] = "retiring"
        sec["keys"] = [e for e in sec["keys"] if e.get("kid") not in (old["kid"], new["kid"])] + [old, new]
        _set_active(sec, new)
        save_credentials(data, path)
        try:
            _retire_pending(client, passport, "", data, path, self_proof=True)
        except httpx.TransportError as exc:  # the new key is active: rotated; the daily job finishes the retire
            logger.warning("agent keys: retire of the old key deferred (%s)", type(exc).__name__)
    return {"status": "rotated", "kid": new["kid"], "old_kid": old["kid"], "via": "agent_key"}


def reset(*, transport: httpx.BaseTransport | None = None, path: Path | None = None,
          reason: str = "recovery", owner_token: Any = None, passport: str = "",
          revoke_old: bool = True) -> dict[str, Any]:
    """Owner recovery for a lost or compromised key.

    1. A FRESH owner sign-in (``prompt=login``; ``owner_token`` is the
       callable that produces it, default ``fresh_owner_token``) — never the
       stored session.
    2. Register a brand-new key with that hub token + PoP. Eternitas flags it
       ``registered_via: owner_recovery`` (``reason="handover"`` →
       ``owner_handover``), notifies platforms, emails the owner and allows 2
       owner-path registrations per passport per 24h (429 → ``rate_limited``).
       A sign-in older than 10 minutes is 401 ``stale_auth_time``.
    3. Revoke every other kid Eternitas lists as active (the public JWKS, so a
       key lost with an old device is caught too) plus any in the local file.

    Registering before revoking means a refused reset never leaves the agent
    with no key at all. The one exception: Eternitas holds at most 2 active
    keys, so if both slots are taken (409 ``too_many_active_keys``) the old
    keys are revoked first and the registration is retried once.

    ``revoke_old=False`` (``windy bring-home``) only registers the new key:
    the cloud body's key is its platform's to revoke when it stands down,
    and a handover that then fails must leave the cloud agent working. It
    never frees a slot by revoking, so a full passport is reported as is."""
    path = path or credentials_path()
    try:
        with _LOCK:
            passport = passport or current_passport()
            if not passport:
                return {"status": "no_passport"}
            get_token = owner_token or fresh_owner_token
            try:
                hub = str(get_token() or "")
            except Exception as exc:
                logger.warning("agent keys: owner sign-in failed (%s)", type(exc).__name__)
                return {"status": "needs_login", "error": str(exc)[:200]}
            if not hub:
                return {"status": "needs_login"}
            data = load_credentials(path)
            sec = _section(data)
            local_kids = {str(e["kid"]) for e in sec["keys"] if e.get("kid")}
            key = generate_private_key()
            new = _new_entry(key)
            revoke_reason = f"owner {reason} (windy agent-key reset)"
            revoked: list[str] = []
            failed: list[str] = []
            with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:

                def old_kids() -> set[str]:
                    old = set(local_kids)
                    try:
                        listed = client.get(f"{_base_url()}/api/v1/bots/{passport}/keys")
                        if listed.status_code == 200:
                            old |= {str(k.get("kid")) for k in listed.json().get("keys", [])
                                    if k.get("status") in ("active", None) and k.get("kid")}
                    except Exception:
                        pass
                    old.discard(new["kid"])
                    return old - set(revoked)

                def revoke_all(kids: set[str]) -> None:
                    for kid in sorted(kids):
                        r = _post(client, f"/api/v1/bots/{passport}/keys/{kid}/revoke", hub,
                                  {"reason": revoke_reason})
                        # 404 key_not_found / 409 already_revoked: already gone
                        (revoked if r.status_code in (200, 204, 404, 409) else failed).append(kid)

                resp = _register(client, passport, key, hub, reason=reason)
                if (revoke_old and resp.status_code == 409
                        and error_code(resp)[0] == "too_many_active_keys"):
                    revoke_all(old_kids())
                    resp = _register(client, passport, key, hub, reason=reason)
                code = error_code(resp)[0]
                if resp.status_code == 429:
                    logger.warning("agent keys: reset refused (%s): 2 owner resets per passport per 24h",
                                   code or "429")
                    return {"status": "rate_limited", "code": code, "revoked": revoked}
                if code == "stale_auth_time":
                    return {"status": "stale_auth", "code": code, "revoked": revoked}
                if resp.status_code not in (200, 201):
                    return {**_http_fail("owner key registration", resp), "revoked": revoked}
                new["registered_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                sec["keys"] = [new]
                _set_active(sec, new)
                save_credentials(data, path)
                if revoke_old:
                    revoke_all(old_kids())
            if failed:
                logger.warning("agent keys: reset could not revoke %d old key(s); run "
                               "`windy agent-key revoke --kid …`", len(failed))
            return {"status": "reset", "kid": new["kid"], "revoked": revoked, "revoke_failed": failed}
    except _Unsupported:
        return _note_unsupported()
    except Exception as exc:
        logger.warning("agent keys: reset failed (%s)", type(exc).__name__)
        return {"status": "failed", "error": type(exc).__name__}


def revoke(kid: str, *, reason: str = "revoked by owner",
           transport: httpx.BaseTransport | None = None, path: Path | None = None,
           passport: str = "", bearer: str = "") -> dict[str, Any]:
    """Revoke one kid (agent's own EPT, else the owner). The key is removed from
    the file; if it was the active key, the next boot generates a fresh one."""
    path = path or credentials_path()
    try:
        with _LOCK:
            passport = passport or current_passport()
            if not passport:
                return {"status": "no_passport"}
            via = "owner_given"
            if not bearer:
                bearer, via = _bearer(passport)
            if not bearer:
                bearer, via = stored_hub_token(), "owner_hub_login"
            if not bearer:
                return {"status": "needs_login"}
            with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
                resp = _post(client, f"/api/v1/bots/{passport}/keys/{kid}/revoke", bearer, {"reason": reason})
            already = resp.status_code == 409 and error_code(resp)[0] == "already_revoked"
            if resp.status_code not in (200, 204) and not already:
                return _http_fail("key revoke", resp)
            data = load_credentials(path)
            sec = _section(data)
            was_active = sec.get("kid") == kid
            sec["keys"] = [e for e in sec["keys"] if e.get("kid") != kid]
            if was_active:
                sec.pop("private_key", None)
                sec.pop("kid", None)
            save_credentials(data, path)
            return {"status": "revoked", "kid": kid, "was_active": was_active, "via": via}
    except _Unsupported:
        return _note_unsupported()
    except Exception as exc:
        logger.warning("agent keys: revoke failed (%s)", type(exc).__name__)
        return {"status": "failed", "error": type(exc).__name__}


def sign_artifact(payload: bytes, *, path: Path | None = None,
                  signed_at: str | None = None) -> str:
    """Detached JWS over ``payload`` with the ACTIVE key (spec §3).

    Protected header: ``{alg, typ: eternitas-sig+jws, kid, passport, signed_at}``.
    kid and passport are both required and bound by the signature. Raises
    ``RuntimeError`` if there is no active key or no passport; callers decide
    how to surface that."""
    passport = current_passport()
    if not passport:
        raise RuntimeError("no passport on this agent")
    entry = active_entry(load_credentials(path or credentials_path()))
    if entry is None:
        raise RuntimeError("no active signing key (run `windy agent-key status`)")
    key = load_private_key(entry["private_key"])
    header = {
        "alg": ALG,
        "typ": ARTIFACT_TYP,
        "kid": entry["kid"],
        "passport": passport,
        "signed_at": signed_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return _compact(key, header, payload, detached=True)


def status(*, transport: httpx.BaseTransport | None = None,
           path: Path | None = None, check_remote: bool = True) -> dict[str, Any]:
    """Local view (+ the public JWKS when reachable). Never includes key material."""
    data = load_credentials(path or credentials_path())
    sec = data.get("eternitas") or {}
    keys = [{k: e.get(k) for k in ("kid", "status", "created_at", "registered_at")}
            for e in sec.get("keys") or []]
    out: dict[str, Any] = {"passport": current_passport(), "active_kid": sec.get("kid"),
                           "keys": keys, "remote": None}
    if check_remote and out["passport"]:
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT, transport=transport) as client:
                resp = client.get(f"{_base_url()}/api/v1/bots/{out['passport']}/keys")
            if _is_route_missing(resp):
                out["remote"] = "unsupported"
            elif resp.status_code == 200:
                out["remote"] = {k.get("kid"): k.get("status") for k in resp.json().get("keys", [])}
            else:
                out["remote"] = f"http {resp.status_code}"
        except Exception as exc:
            out["remote"] = f"unreachable ({type(exc).__name__})"
    return out


# ── mode B: short-lived key-bound tokens (Eternitas agent-keys v1 step 4) ──

TOKEN_PATH = "/api/v1/tokens/agent"
_TOKEN_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_TOKEN_LOCK = threading.Lock()


class AgentTokenError(Exception):
    """A refused or failed mode B exchange. ``code`` is Eternitas's error code
    (dpop_required, invalid_dpop_proof, passport_suspended, passport_revoked,
    passport_not_active, passport_not_born, unknown_audience, ...) or ``no_key`` /
    ``http_<n>`` / ``unreachable``; ``retry_after`` is set whenever Eternitas sends
    Retry-After (429, and 409 ``passport_not_born``)."""

    def __init__(self, code: str, message: str = "", status: int = 0,
                 retry_after: float | None = None) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after


# Birth reservation v1 (eternitas #265, docs/spec/birth-reservation.v1.md section 2): mid-canal, every credential
# route answers 409 passport_not_born + Retry-After: 30. It is NOT a refusal of standing: whoever runs the agent
# waits quietly and retries. Never revoked wording, never a lifeboat.
NOT_BORN = "passport_not_born"


def is_not_born(code: str | None) -> bool:
    """True for Eternitas's 'this passport is still being born' answer."""
    return code == NOT_BORN


def retry_after_s(resp: httpx.Response) -> float | None:
    """Retry-After in seconds (delta form), else None."""
    try:
        return float(resp.headers.get("Retry-After", "") or 0) or None
    except ValueError:
        return None


def _active_key(cred_path: Path | None) -> tuple[ec.EllipticCurvePrivateKey, str]:
    entry = active_entry(load_credentials(cred_path or credentials_path()))
    if entry is None or not entry.get("registered_at"):
        raise AgentTokenError("no_key", "no registered agent key (run `windy agent-key ensure`)")
    return load_private_key(entry["private_key"]), str(entry["kid"])


# Refresh a cached token this long before it expires, so no request waits on a mint at the edge; if that refresh
# hits a TRANSIENT failure (network, 5xx, 429) the still-valid cached token keeps working until STALE_GRACE_S before
# expiry. Zero, 2026-11-01: ~126 mints died before reaching Eternitas (network) and each fell back to the legacy EPT.
REFRESH_EARLY_S = 90
STALE_GRACE_S = 5
MINT_RETRY_S = 0.5


def _transient(err: "AgentTokenError") -> bool:
    return err.code == "unreachable" or err.status == 429 or err.status >= 500


def _mint(url: str, aud: str, key: Any, transport: httpx.BaseTransport | None) -> httpx.Response:
    try:
        with httpx.Client(timeout=15.0, transport=transport) as client:
            return client.post(url, json={"aud": aud}, headers={"DPoP": dpop_proof(key, "POST", url)})
    except httpx.HTTPError as exc:
        # The class (ConnectError, ConnectTimeout, ReadTimeout...) says where it died; never a URL or a token.
        raise AgentTokenError("unreachable", type(exc).__name__) from exc


def request_agent_token(aud: str, *, transport: httpx.BaseTransport | None = None,
                        cred_path: Path | None = None, min_ttl: int = REFRESH_EARLY_S) -> dict[str, Any]:
    """An ``EPT+agent`` for ``aud`` (a Windy service name such as ``windy-calendar``),
    bound to this agent's active registered key (``cnf.jkt``), with LIVE ei/band/clr.

    Auth is ONLY a DPoP proof by that key: no bearer, so a leaked EPT can't mint these.
    Cached per (kid, aud) until ``min_ttl`` seconds before expiry (tokens live ≤5 min;
    Eternitas allows 30 mints/min per passport). A TRANSIENT mint failure (network, 5xx, 429) returns the cached
    token while it is still valid; with nothing cached, a network failure is retried once after MINT_RETRY_S.
    Any other refusal (revoked, suspended...) drops the cached token and raises. Returns Eternitas's body
    ``{token, token_type, expires_in, aud, passport}`` plus ``expires_at`` (epoch s).
    Raises :class:`AgentTokenError`. Services that take the token for WRITES also want a
    per-request proof: see :func:`service_dpop`.
    """
    key, kid = _active_key(cred_path)
    now = time.time()
    with _TOKEN_LOCK:
        hit = _TOKEN_CACHE.get((kid, aud))
        if hit and hit["expires_at"] - min_ttl > now:
            return dict(hit)
    url = f"{_base_url()}{TOKEN_PATH}"
    usable = hit if hit and hit["expires_at"] - STALE_GRACE_S > now else None
    try:
        try:
            resp = _mint(url, aud, key, transport)
        except AgentTokenError:
            if usable is not None:
                raise
            time.sleep(MINT_RETRY_S)  # nothing cached to fall back on: one more try
            resp = _mint(url, aud, key, transport)
        if resp.status_code != 200:
            code, msg = error_code(resp)
            _standing_from_refusal(resp, code)
            raise AgentTokenError(code or f"http_{resp.status_code}", msg, resp.status_code, retry_after_s(resp))
    except AgentTokenError as err:
        if usable is not None and _transient(err):
            logger.info("agent keys: refresh for %s failed (%s %s); the cached token is still valid", aud,
                        err.code, err.message)
            return dict(usable)
        if not _transient(err):
            with _TOKEN_LOCK:
                _TOKEN_CACHE.pop((kid, aud), None)  # revoked/suspended/...: never keep using it
        raise
    body = resp.json()
    token = str(body.get("token") or "")
    if not token:
        raise AgentTokenError("bad_response", "no token in 200 body", 200)
    # Never use a token bound to some other key: cnf.jkt must be OUR kid.
    jkt = (_claims(token).get("cnf") or {}).get("jkt")
    if jkt != kid:
        raise AgentTokenError("jkt_mismatch", "token cnf.jkt != this agent's kid", 200)
    out = dict(body)
    out["expires_at"] = now + min(int(body.get("expires_in") or 300), 300)
    with _TOKEN_LOCK:
        _TOKEN_CACHE[(kid, aud)] = out
    _standing_good_mint()
    return dict(out)


def _refusal_fields(resp: httpx.Response) -> dict[str, Any]:
    """The extra fields of an Eternitas refusal (retired_until, reason), from ``detail`` or the top level."""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(body, dict):
        return {}
    detail: dict[str, Any] = body["detail"] if isinstance(body.get("detail"), dict) else {}
    return {k: detail.get(k, body.get(k)) for k in ("retired_until", "reason")}


def _standing_from_refusal(resp: httpx.Response, code: str) -> None:
    """A refusal that means the agent may not run (retired, suspended, revoked, this key revoked) latches the stop
    (windyfly.agent.standing). Any other refusal changes nothing."""
    try:
        from windyfly.agent import standing

        if standing.kind_of(code):
            standing.latch(code, **_refusal_fields(resp))
    except Exception as exc:  # noqa: BLE001 — the refusal itself is still raised
        logger.warning("standing: could not record the stop (%s)", type(exc).__name__)


def _standing_good_mint() -> None:
    try:
        from windyfly.agent import standing

        if standing.stopped():
            standing.clear()
    except Exception as exc:  # noqa: BLE001
        logger.debug("standing: %s", type(exc).__name__)


def service_dpop(htm: str, url: str, *, cred_path: Path | None = None) -> str:
    """Per-request DPoP proof (fresh iat + unique jti) for calling a Windy service
    with an ``EPT+agent``: send ``Authorization: Bearer <token>`` plus ``DPoP: <this>``.
    ``url`` is the absolute URL of THAT request; the proof signs :func:`htu` of it."""
    key, _ = _active_key(cred_path)
    return dpop_proof(key, htm, htu(url))


PICKUP_PROOF_TYP = "windy-pickup-proof+jwt"


def pickup_proof(handover_id: str, htm: str, url: str, *, path: Path | None = None) -> str:
    """The desktop door's proof that THIS computer holds the round's key (home handover desktop pickup code, P5):
    a compact ES256 JWS, header {alg, typ windy-pickup-proof+jwt, kid, jwk}, payload {handover_id, htm, htu, iat,
    jti}. ``htu`` is the full URL without query (the Hub compares the path; one signer for this and DPoP). The jwk
    rides every proof (the Hub needs it on the first; on later ones it is checked against the kept key). The key
    may still be UNREGISTERED: it is the one whose kid started the round."""
    entry = active_entry(load_credentials(path or credentials_path()))
    if entry is None:
        raise AgentTokenError("no_key", "no local agent key for this round")
    key = load_private_key(entry["private_key"])
    header = {"alg": ALG, "typ": PICKUP_PROOF_TYP, "kid": str(entry["kid"]), "jwk": public_jwk(key)}
    claims = {"handover_id": handover_id, "htm": htm.upper(), "htu": htu(url), "iat": int(time.time()),
              "jti": _b64u(os.urandom(16))}
    return _compact(key, header, json.dumps(claims, separators=(",", ":")).encode("utf-8"))


def bus_dpop(htm: str, htu_raw: str, *, cred_path: Path | None = None) -> str:
    """A DPoP proof whose htu is signed VERBATIM (no URL normalising), for a non-HTTP target such as Windy Code's
    IDE agent bus ("urn:windycode:agent-bus", compared byte for byte; Eternitas ruled 11-04). Fresh iat + jti."""
    key, _ = _active_key(cred_path)
    return dpop_proof(key, htm, htu_raw)


def htu(url: str) -> str:
    """The DPoP ``htu`` for ``url``: exactly what httpx will send. Scheme and host (default port dropped), then
    the percent-encoded path with dot segments removed, with no query or fragment (RFC 9449). Signing the raw
    string instead would sign "a b" while "a%20b" goes on the wire, and a server that checks the encoded path
    (Windy Cloud since its htu fix) refuses that."""
    u = httpx.URL(url)
    path = u.raw_path.split(b"?", 1)[0].decode("ascii")
    return f"{u.scheme}://{u.netloc.decode('ascii')}{path}"


def clear_token_cache() -> None:
    with _TOKEN_LOCK:
        _TOKEN_CACHE.clear()


# ── background / hooks ───────────────────────────────────────────────

def disabled() -> bool:
    """Automatic key work is off under pytest and with WINDY_DISABLE_AGENT_KEYS
    (explicit `windy agent-key …` commands still run)."""
    return bool(os.environ.get("WINDY_DISABLE_AGENT_KEYS") or os.environ.get("PYTEST_CURRENT_TEST"))


def ensure_in_background() -> threading.Thread | None:
    """Boot: fire-and-forget. Skipped under pytest and WINDY_DISABLE_AGENT_KEYS."""
    if disabled():
        return None
    t = threading.Thread(target=ensure_registered, name="agent-keys", daemon=True)
    t.start()
    return t


def register_after_signin() -> dict[str, Any]:
    """Hook for `windy login` and a completed hatch: a synchronous best-effort
    ensure_registered (the CLI process may exit right after). Never raises."""
    if disabled():
        return {"status": "skipped"}
    return ensure_registered()
