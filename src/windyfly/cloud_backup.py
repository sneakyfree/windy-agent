"""Cold storage backup — back up agent memory to Windy Cloud.

Encrypts and uploads windyfly.db to Windy Cloud (Cloudflare R2) on a
configurable schedule. Restore lives in windyfly/backup_restore.py (asks first, restores through the live
connection, keeps an encrypted undo).

Config (windyfly.toml):
    [cloud]
    auto_backup = true
    backup_interval = "24h"

Windy Cloud archive contract (canonical, 2026-07-04):
    POST /api/v1/archive/agent                    — multipart upload (file + metadata + filename)
    GET  /api/v1/archive/list/windy_fly           — list backups (newest first)
    GET  /api/v1/archive/retrieve/windy_fly/{name} — download one by filename

Encryption is AES-256-GCM, key from WINDY_BACKUP_KEY (zero-knowledge if
set) or passport-derived (convenient, not zero-knowledge) — see
_get_encryption_key.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from windyfly.memory.snapshot import snapshot_bytes
from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)

PROJECT_ROOT = get_project_root()
_BACKUP_STATE_FILE = PROJECT_ROOT / "data" / "backup_state.json"
_RETENTION_COUNT = 5  # keep the 5 most recent backups (Cloud enforces)
# Scheduled-backup safety (Hub 10-21, after Zero's 10-19 memory collapse sent near-empty backups to Cloud):
_BACKOFF_S = 6 * 3600          # after a failed scheduled upload, wait this long before trying again
_GATE_RATIO = 0.5              # hold a backup whose memory has under half the episodes of the last good one
_GATE_MIN_BASELINE = 20        # ...only when that last good backup had at least this many (small agents: noise)
_NOTICE_EVERY_S = 24 * 3600    # while held: one ERROR line + one owner notice per day, not per hourly check
_HOLD_RELEASE_S = 72 * 3600    # unanswered for this long: upload anyway marked suspect, ONLY when the switch is on
SUSPECT_UPLOAD_ENV = "WINDY_BACKUP_SUSPECT_UPLOAD"  # off until Windy Cloud's suspect-rotation rule is live
_derived_key_refusals = 0  # uploads refused for want of an owner-held key (counts only)

# A backup is a BULK transfer, not an interactive call. The old flat
# 60s httpx timeout applied to writes too, so on a normie's home/office
# uplink (measured ~270 KB/s on Windy 0) a large DB upload hit
# httpx.WriteTimeout at ~16 MB — and WriteTimeout stringifies to "",
# which is how this failed silently for days (see 2026-07-06 audit +
# _describe_error). Give the connect/pool phases a tight bound but let
# the read/write phases run long enough for a multi-MB body over a slow
# link. Compression (below) keeps the real payload ~4x smaller so this
# ceiling is rarely approached, but the headroom matters for a first
# backup on a big DB.
_TIMEOUT = httpx.Timeout(connect=30.0, read=300.0, write=600.0, pool=30.0)


def _describe_error(exc: BaseException) -> str:
    """Never-empty, typed description of an exception.

    The scheduled backup was failing on Windy 0 with a blank reason —
    "Scheduled backup failed: " with nothing after it — because several
    exception types (a bare ``raise SomeError()``, some httpx/SSL errors,
    a ``TrustDenied`` raised without a message) stringify to "". A blank
    error is undiagnosable, which is exactly why the failure sat
    unexplained. Always prefix with the exception class so the reason is
    at minimum identifiable, even when the message is empty.
    """
    msg = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {msg}" if msg else name


def _get_cloud_url(config: dict | None = None) -> str:
    """Get Windy Cloud URL from config or env."""
    if config:
        url = config.get("ecosystem", {}).get("windy_cloud_url", "")
        if url:
            return url.rstrip("/")
    return os.environ.get("WINDY_CLOUD_URL", "https://cloud.windycloud.com").rstrip("/")


def _get_cloud_token() -> str:
    """Get auth token for Windy Cloud."""
    return os.environ.get("WINDY_CLOUD_TOKEN", "") or os.environ.get("WINDY_JWT", "")


# FROZEN. Part of the passport-derived backup key (material = "<passport>:<name>").
# This is NOT the brand: renaming the product or the default agent name must never
# change it, or every existing backup made with the default name becomes
# undecryptable. A known-answer test (tests/test_backup_kdf_frozen.py) guards it.
_KDF_DEFAULT_AGENT_NAME = "Windy Fly"


def _get_encryption_key() -> bytes:
    """Derive the 32-byte backup key.

    Prefers a user-held secret ``WINDY_BACKUP_KEY`` — set it (e.g. from
    the Eternitas recovery phrase) for a genuinely zero-knowledge backup
    the cloud cannot decrypt. Otherwise falls back to a passport-derived
    key: convenient (restore "just works" on a new device with the same
    passport) but NOT zero-knowledge, since the passport + agent name are
    semi-public. Either way the key feeds AES-256-GCM below.
    """
    user_secret = os.environ.get("WINDY_BACKUP_KEY", "")
    if user_secret:
        return hashlib.pbkdf2_hmac(
            "sha256", user_secret.encode(), b"windy-backup-kdf-v2", 200_000
        )
    passport = os.environ.get("ETERNITAS_PASSPORT", "windyfly-local")
    agent_name = os.environ.get("WINDYFLY_AGENT_NAME", _KDF_DEFAULT_AGENT_NAME)
    material = f"{passport}:{agent_name}".encode()
    return hashlib.pbkdf2_hmac("sha256", material, b"windy-backup-kdf-v2", 200_000)


_GZIP_MAGIC = b"\x1f\x8b"


def _maybe_decompress(data: bytes) -> bytes:
    """Gunzip if the payload is gzip-framed, else return as-is.

    Backups are gzipped before encryption (a 122 MB SQLite DB compressed
    ~4.3x on Windy 0), so restore must decompress. Detect by gzip magic
    bytes rather than a metadata flag: the retrieve endpoint returns only
    the file bytes, and magic-byte detection restores BOTH new
    (compressed) and pre-compression (raw SQLite, magic ``SQLite\x20``)
    backups transparently — no metadata round-trip, backward compatible.
    """
    if data[:2] == _GZIP_MAGIC:
        return gzip.decompress(data)
    return data


_GCM_NONCE_BYTES = 12


def _encrypt_data(data: bytes, key: bytes) -> bytes:
    """AES-256-GCM. Output = nonce(12) || ciphertext+tag.

    Replaces the pre-2026-07-04 SHA-256-keystream XOR, which had no
    integrity tag and reused the keystream structure across backups.
    GCM gives authenticated encryption; a fresh random nonce per backup.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(_GCM_NONCE_BYTES)
    ct = AESGCM(key).encrypt(nonce, data, None)
    return nonce + ct


def _decrypt_data(data: bytes, key: bytes) -> bytes:
    """Inverse of _encrypt_data. Raises on a bad tag (tamper/wrong key)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(data) < _GCM_NONCE_BYTES + 16:
        raise ValueError("backup ciphertext too short")
    nonce, ct = data[:_GCM_NONCE_BYTES], data[_GCM_NONCE_BYTES:]
    return AESGCM(key).decrypt(nonce, ct, None)



async def _cloud_send(
    client: Any, method: str, url: str, fallback_header: dict, where: str, **kw: Any,
) -> Any:
    """One Cloud request: the agent's EPT+agent (+ DPoP on writes) first; on no token, or a
    401/403 to it, the old credential (bot key / WINDY_CLOUD_TOKEN / WINDY_JWT) once.
    See windyfly.agent.cloud_auth (Cloud KE3)."""
    from windyfly.agent import cloud_auth

    # client.post / client.get, as the archive contract tests fake them
    send = getattr(client, method.lower())
    agent = await asyncio.to_thread(cloud_auth.agent_headers, method, url)
    resp = await send(url, headers=agent or fallback_header, **kw)
    if agent and fallback_header and cloud_auth.refused(resp.status_code):
        cloud_auth.fallback_used(where, resp.status_code)
        resp = await send(url, headers=fallback_header, **kw)
    return resp


async def _no_credential(auth_header: dict, cloud_url: str) -> bool:
    """True when there is neither an old credential nor a mintable EPT+agent: then nothing
    is sent and the old 'No cloud token configured' result stands (Hub's #494 review).
    The mint is cached, so this costs no extra call to Eternitas."""
    if auth_header:
        return False
    from windyfly.agent import cloud_auth

    return await asyncio.to_thread(cloud_auth.agent_headers, "GET", cloud_url) is None


def owner_key_missing() -> bool:
    """True when the agent has no owner-held WINDY_BACKUP_KEY: then no backup is uploaded (Boss 10-17)."""
    return not os.environ.get("WINDY_BACKUP_KEY", "")


def _db_path(config: dict | None) -> Path:
    db_path = PROJECT_ROOT / "data" / "windyfly.db"
    if config:
        db_path = Path(config.get("memory", {}).get("db_path", "data/windyfly.db"))
    return db_path


def episode_count(db_path: Path) -> int | None:
    """How many episodes the memory holds (a short read-only connection), or None if it can't be read."""
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            return int(con.execute("SELECT count(*) FROM episodes").fetchone()[0])
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return None


def accept_memory(config: dict | None = None) -> int | None:
    """The owner says the smaller memory is expected: the next scheduled check uploads it and it becomes the new
    baseline. Returns the episode count accepted (None if the memory can't be read)."""
    n = episode_count(_db_path(config))
    if n is None:
        return None
    state = get_backup_state()
    state["accept_episodes"] = n
    _save_backup_state(state)
    return n


def hold_status() -> dict | None:
    """The current hold (since, baseline, now), or None when no backup is held."""
    hold = get_backup_state().get("hold")
    return hold if isinstance(hold, dict) and hold.get("since") else None


async def backup_to_cloud(config: dict | None = None, *, suspect: bool = False) -> dict:
    """Encrypt and upload the agent database to Windy Cloud.

    Returns dict with backup_id, size, and timestamp on success.
    """
    from windyfly.auth.audit import audit_bot_key_call
    from windyfly.auth.bot_credentials import ecosystem_auth_header, get_bot_key
    from windyfly.trust.gate import TrustDenied, require_trust

    # Boss 10-17: no backup leaves under the key derived from passport + agent name. Both are sent
    # to Cloud with the upload, so Cloud could open it. Only an owner-held WINDY_BACKUP_KEY uploads.
    # (Restore still opens old backups with whichever key made them.)
    if owner_key_missing():
        global _derived_key_refusals
        _derived_key_refusals += 1
        logger.warning("backup not sent: no WINDY_BACKUP_KEY; a derived key is never used for an upload "
                       "(refused %d time(s) since start)", _derived_key_refusals)
        return {"success": False,
                "error": "Backup not sent: this agent has no backup key of its own (WINDY_BACKUP_KEY). "
                         "A key derived from the passport is not used for uploads."}

    try:
        await require_trust("upload_file")
    except TrustDenied as denied:
        return {"success": False, "error": _describe_error(denied) + " (action=upload_file)"}

    cloud_url = _get_cloud_url(config)
    auth_header = await ecosystem_auth_header(fallback_token=_get_cloud_token())

    if await _no_credential(auth_header, cloud_url):
        return {"success": False, "error": "No cloud token configured"}

    cred = await get_bot_key()
    audit_key_id = cred.key_id if cred else ""

    db_path = _db_path(config)

    if not db_path.exists():
        return {"success": False, "error": f"Database not found: {db_path}"}

    # A consistent snapshot through SQLite, never a file copy: copying the
    # live file dropped this process's locks (the 2026-10-15 WAL split on
    # Windy 0) and left out every write still in the WAL.
    try:
        raw_data = snapshot_bytes(db_path)
    except Exception as e:
        return {"success": False, "error": "Could not snapshot the database: " + _describe_error(e)}

    # Compress THEN encrypt. SQLite compresses well (122 MB → 28 MB,
    # ~4.3x, on Windy 0), and a smaller payload is the biggest lever
    # against the slow-uplink WriteTimeout that was silently killing
    # backups — plus it's less R2 to store/transfer. Order matters:
    # encrypted bytes are incompressible, so gzip must come first.
    # Restore reverses via _maybe_decompress (gzip-magic detection).
    key = _get_encryption_key()
    compressed = gzip.compress(raw_data)
    encrypted = _encrypt_data(compressed, key)
    checksum = hashlib.sha256(raw_data).hexdigest()  # of the ORIGINAL db

    # Deterministic, sortable filename so list→retrieve round-trips.
    ts = datetime.now(timezone.utc)
    backup_name = f"windyfly-{ts.strftime('%Y%m%dT%H%M%SZ')}.enc"
    metadata = json.dumps({
        "encrypted": True,
        "compressed": "gzip",
        "checksum_sha256": checksum,
        "size_bytes": len(raw_data),
        "compressed_bytes": len(compressed),
        "agent_name": os.environ.get("WINDYFLY_AGENT_NAME", "Windy Fly"),
        "passport_id": os.environ.get("ETERNITAS_PASSPORT", ""),
        "timestamp": ts.isoformat(),
        # keep a few backups; Cloud enforces retention server-side
        "retention_count": _RETENTION_COUNT,
        "episodes": episode_count(db_path),
        # Windy Cloud's rotation never lets a suspect backup push out the newest good one (its rule A/B).
        **({"suspect": True} if suspect else {}),
    })

    try:
        # Canonical archive contract (2026-07-04): multipart upload to
        # /archive/agent, NOT the old JSON body the server never accepted.
        target_url = f"{cloud_url}/api/v1/archive/agent"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            with audit_bot_key_call(
                key_id=audit_key_id,
                scope_used="cloud:upload",
                target_url=target_url,
            ) as ctx:
                resp = await _cloud_send(
                    client, "POST", target_url, auth_header, "backup upload",
                    files={"file": (backup_name, encrypted, "application/octet-stream")},
                    data={"metadata": metadata, "filename": backup_name},
                )
                ctx["response_status"] = resp.status_code
            resp.raise_for_status()
            result = resp.json()

            # A fresh state: the hold, the failure backoff and any owner accept are all settled by this upload,
            # and its episode count is the baseline the next backups are compared with.
            _save_backup_state({
                "last_backup": ts.isoformat(),
                "backup_id": backup_name,
                "file_id": result.get("file_id", ""),
                "size_bytes": len(raw_data),
                "checksum": checksum,
                "episodes": json.loads(metadata).get("episodes"),
                **({"suspect": True} if suspect else {}),
            })

            logger.info("Backup uploaded: %s (%d bytes)", backup_name, len(raw_data))
            return {
                "success": True,
                "backup_id": backup_name,
                "file_id": result.get("file_id", ""),
                "size_bytes": len(raw_data),
            }

    except httpx.ConnectError:
        return {"success": False, "error": f"Cannot reach Windy Cloud at {cloud_url}"}
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"Upload failed: {e.response.status_code}"}
    except Exception as e:
        return {"success": False, "error": _describe_error(e)}


async def list_backups(config: dict | None = None) -> dict:
    """List available backups from Windy Cloud."""
    from windyfly.auth.audit import audit_bot_key_call
    from windyfly.auth.bot_credentials import ecosystem_auth_header, get_bot_key

    cloud_url = _get_cloud_url(config)
    auth_header = await ecosystem_auth_header(fallback_token=_get_cloud_token())

    if await _no_credential(auth_header, cloud_url):
        return {"success": False, "backups": [], "error": "No cloud token configured"}

    cred = await get_bot_key()
    audit_key_id = cred.key_id if cred else ""

    try:
        # Canonical list endpoint (2026-07-04). Server returns
        # {product, count, files:[{filename, size_bytes, created_at,...}]}
        # newest-first; normalize the key to "backups" for callers.
        target_url = f"{cloud_url}/api/v1/archive/list/windy_fly"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            with audit_bot_key_call(
                key_id=audit_key_id,
                scope_used="cloud:download",
                target_url=target_url,
            ) as ctx:
                resp = await _cloud_send(client, "GET", target_url, auth_header, "backup read")
                ctx["response_status"] = resp.status_code
            resp.raise_for_status()
            data = resp.json()
            return {"success": True, "backups": data.get("files", [])}
    except Exception as e:
        return {"success": False, "backups": [], "error": _describe_error(e)}


def _save_backup_state(state: dict) -> None:
    """Save last backup info to disk."""
    _BACKUP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _BACKUP_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_backup_state() -> dict:
    """Get the last backup state."""
    if not _BACKUP_STATE_FILE.exists():
        return {"last_backup": None}
    try:
        return json.loads(_BACKUP_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_backup": None}


def should_backup(config: dict | None = None) -> bool:
    """Check if a backup is due based on the configured interval."""
    cloud_cfg = config.get("cloud", {}) if config else {}
    if not cloud_cfg.get("auto_backup", True):
        return False

    interval_str = cloud_cfg.get("backup_interval", "24h")
    interval_seconds = _parse_interval(interval_str)

    state = get_backup_state()
    last = state.get("last_backup")
    if not last:
        return True

    try:
        last_dt = datetime.fromisoformat(last)
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
        return elapsed >= interval_seconds
    except (ValueError, TypeError):
        return True


def _parse_interval(interval: str) -> float:
    """Parse an interval string like '24h', '12h', '1d' to seconds."""
    interval = interval.strip().lower()
    if interval.endswith("d"):
        return float(interval[:-1]) * 86400
    if interval.endswith("h"):
        return float(interval[:-1]) * 3600
    if interval.endswith("m"):
        return float(interval[:-1]) * 60
    return float(interval)


async def _gate(config: dict | None, state: dict, now: float) -> str:
    """"ok", "hold" or "suspect" for a DUE scheduled backup (see the _GATE_* constants)."""
    n = episode_count(_db_path(config))
    baseline = state.get("episodes")
    if state.get("accept_episodes") is not None:
        return "ok"  # the owner accepted the smaller memory: this upload becomes the new baseline
    if n is None or not isinstance(baseline, int) or baseline < _GATE_MIN_BASELINE or n >= baseline * _GATE_RATIO:
        if state.pop("hold", None) is not None:
            _save_backup_state(state)
        return "ok"
    raw_hold = state.get("hold")
    hold: dict[str, Any] = raw_hold if isinstance(raw_hold, dict) else {}
    since = float(hold.get("since") or now)
    if now - since >= _HOLD_RELEASE_S and os.environ.get(SUSPECT_UPLOAD_ENV, "") == "1":
        return "suspect"
    last_notice = float(hold.get("last_notice_at") or 0)
    if now - last_notice >= _NOTICE_EVERY_S:
        text = (f"Your agent's memory has {n} episodes; its last backup had {baseline}. I'm holding the backup so it "
                "doesn't push out the good ones. If this is expected, run /backup accept in your agent's terminal "
                "(or ask whoever runs your agent to).")
        logger.error("BACKUP HELD: memory %d episodes vs %d in the last backup (under %d%%)",
                     n, baseline, int(_GATE_RATIO * 100))
        try:
            from windyfly.agent.owner_mail import send

            await send("Your agent's backup is on hold", text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("backup hold: owner notice not sent (%s)", type(exc).__name__)
        last_notice = now
    state["hold"] = {"since": since, "baseline": baseline, "now": n, "last_notice_at": last_notice}
    _save_backup_state(state)
    return "hold"


async def run_backup_if_due(config: dict | None = None) -> dict | None:
    """Run backup if the interval has elapsed. Called by the scheduler (hourly).

    A failed upload waits _BACKOFF_S before the next try; a memory that collapsed is HELD (_gate) rather than
    uploaded over the good backups. ``/backup now`` (backup_to_cloud directly) is the owner's own choice and
    is never held."""
    if not should_backup(config):
        return None

    token = _get_cloud_token()
    if not token:
        logger.debug("Backup skipped: no cloud token")
        return None

    state = get_backup_state()
    now = time.time()
    last_failure = state.get("last_failure_at")
    if isinstance(last_failure, (int, float)) and now - last_failure < _BACKOFF_S:
        return None  # backing off after a failure (logged once, when it failed)

    decision = await _gate(config, state, now)
    if decision == "hold":
        return None

    logger.info("Starting scheduled backup to Windy Cloud%s...", " (marked SUSPECT after 72 h on hold)"
                if decision == "suspect" else "")
    result = await backup_to_cloud(config, suspect=decision == "suspect")
    if result.get("success"):
        logger.info("Scheduled backup complete")
    else:
        state = get_backup_state()
        state["last_failure_at"] = now
        _save_backup_state(state)
        logger.warning("Scheduled backup failed: %s (next try in %d h)", result.get("error"), _BACKOFF_S // 3600)
    return result
