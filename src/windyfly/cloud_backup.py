"""Cold storage backup — back up agent memory to Windy Cloud.

Encrypts and uploads windyfly.db to Windy Cloud (Cloudflare R2) on a
configurable schedule. Supports restore for device migration and
disaster recovery.

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

import gzip
import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)

PROJECT_ROOT = get_project_root()
_BACKUP_STATE_FILE = PROJECT_ROOT / "data" / "backup_state.json"
_RETENTION_COUNT = 5  # keep the 5 most recent backups (Cloud enforces)

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
    agent_name = os.environ.get("WINDYFLY_AGENT_NAME", "Windy Fly")
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


async def backup_to_cloud(config: dict | None = None) -> dict:
    """Encrypt and upload the agent database to Windy Cloud.

    Returns dict with backup_id, size, and timestamp on success.
    """
    from windyfly.auth.audit import audit_bot_key_call
    from windyfly.auth.bot_credentials import ecosystem_auth_header, get_bot_key
    from windyfly.trust.gate import TrustDenied, require_trust

    try:
        await require_trust("upload_file")
    except TrustDenied as denied:
        return {"success": False, "error": _describe_error(denied) + " (action=upload_file)"}

    cloud_url = _get_cloud_url(config)
    auth_header = await ecosystem_auth_header(fallback_token=_get_cloud_token())

    if not auth_header:
        return {"success": False, "error": "No cloud token configured"}

    cred = await get_bot_key()
    audit_key_id = cred.key_id if cred else ""

    db_path = PROJECT_ROOT / "data" / "windyfly.db"
    if config:
        db_path = Path(config.get("memory", {}).get("db_path", "data/windyfly.db"))

    if not db_path.exists():
        return {"success": False, "error": f"Database not found: {db_path}"}

    # Copy DB to temp file to avoid locking issues
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        shutil.copy2(str(db_path), tmp_path)
        raw_data = Path(tmp_path).read_bytes()
    finally:
        Path(tmp_path).unlink(missing_ok=True)

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
                resp = await client.post(
                    target_url,
                    files={"file": (backup_name, encrypted, "application/octet-stream")},
                    data={"metadata": metadata, "filename": backup_name},
                    headers=auth_header,
                )
                ctx["response_status"] = resp.status_code
            resp.raise_for_status()
            result = resp.json()

            _save_backup_state({
                "last_backup": ts.isoformat(),
                "backup_id": backup_name,
                "file_id": result.get("file_id", ""),
                "size_bytes": len(raw_data),
                "checksum": checksum,
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


async def restore_from_cloud(
    backup_id: str = "latest",
    config: dict | None = None,
) -> dict:
    """Download and decrypt a backup from Windy Cloud.

    Replaces the local database with the restored backup.
    Creates a local backup before overwriting.
    """
    from windyfly.auth.audit import audit_bot_key_call
    from windyfly.auth.bot_credentials import ecosystem_auth_header, get_bot_key

    cloud_url = _get_cloud_url(config)
    auth_header = await ecosystem_auth_header(fallback_token=_get_cloud_token())

    if not auth_header:
        return {"success": False, "error": "No cloud token configured"}

    cred = await get_bot_key()
    audit_key_id = cred.key_id if cred else ""

    db_path = PROJECT_ROOT / "data" / "windyfly.db"
    if config:
        db_path = Path(config.get("memory", {}).get("db_path", "data/windyfly.db"))

    # Resolve "latest" to a real filename via the list endpoint.
    filename = backup_id
    if backup_id in ("", "latest"):
        listed = await list_backups(config)
        backups = listed.get("backups", [])
        if not backups:
            return {"success": False, "error": "No backups found"}
        filename = backups[0]["filename"]  # list is newest-first

    try:
        # Canonical: fetch raw encrypted bytes by filename.
        target_url = f"{cloud_url}/api/v1/archive/retrieve/windy_fly/{filename}"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            with audit_bot_key_call(
                key_id=audit_key_id,
                scope_used="cloud:download",
                target_url=target_url,
            ) as ctx:
                resp = await client.get(target_url, headers=auth_header)
                ctx["response_status"] = resp.status_code
            resp.raise_for_status()
            encrypted = resp.content

            key = _get_encryption_key()
            try:
                decrypted = _maybe_decompress(_decrypt_data(encrypted, key))
            except Exception:
                return {
                    "success": False,
                    "error": "Could not decrypt backup — wrong key or "
                    "corrupted data. If you set WINDY_BACKUP_KEY on the "
                    "source device, set the same value here.",
                }

            # Back up current DB before overwriting
            if db_path.exists():
                backup_path = db_path.with_suffix(".db.pre-restore")
                shutil.copy2(str(db_path), str(backup_path))
                logger.info("Pre-restore backup saved to %s", backup_path)

            # Write restored database
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.write_bytes(decrypted)

            logger.info("Database restored from backup %s (%d bytes)", filename, len(decrypted))
            return {
                "success": True,
                "backup_id": filename,
                "size_bytes": len(decrypted),
                "restored_at": datetime.now(timezone.utc).isoformat(),
            }

    except httpx.ConnectError:
        return {"success": False, "error": f"Cannot reach Windy Cloud at {cloud_url}"}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"success": False, "error": "No backups found"}
        return {"success": False, "error": f"Download failed: {e.response.status_code}"}
    except Exception as e:
        return {"success": False, "error": _describe_error(e)}


async def list_backups(config: dict | None = None) -> dict:
    """List available backups from Windy Cloud."""
    from windyfly.auth.audit import audit_bot_key_call
    from windyfly.auth.bot_credentials import ecosystem_auth_header, get_bot_key

    cloud_url = _get_cloud_url(config)
    auth_header = await ecosystem_auth_header(fallback_token=_get_cloud_token())

    if not auth_header:
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
                resp = await client.get(target_url, headers=auth_header)
                ctx["response_status"] = resp.status_code
            resp.raise_for_status()
            data = resp.json()
            return {"success": True, "backups": data.get("files", [])}
    except Exception as e:
        return {"success": False, "backups": [], "error": _describe_error(e)}


def _save_backup_state(state: dict) -> None:
    """Save last backup info to disk."""
    _BACKUP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _BACKUP_STATE_FILE.write_text(json.dumps(state, indent=2))


def get_backup_state() -> dict:
    """Get the last backup state."""
    if not _BACKUP_STATE_FILE.exists():
        return {"last_backup": None}
    try:
        return json.loads(_BACKUP_STATE_FILE.read_text())
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


async def run_backup_if_due(config: dict | None = None) -> dict | None:
    """Run backup if the interval has elapsed. Called by the scheduler."""
    if not should_backup(config):
        return None

    token = _get_cloud_token()
    if not token:
        logger.debug("Backup skipped: no cloud token")
        return None

    logger.info("Starting scheduled backup to Windy Cloud...")
    result = await backup_to_cloud(config)
    if result.get("success"):
        logger.info("Scheduled backup complete")
    else:
        logger.warning("Scheduled backup failed: %s", result.get("error"))
    return result
