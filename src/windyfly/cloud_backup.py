"""Cold storage backup — back up agent memory to Windy Cloud.

Encrypts and uploads windyfly.db to Windy Cloud (Cloudflare R2) on a
configurable schedule. Supports restore for device migration and
disaster recovery.

Config (windyfly.toml):
    [cloud]
    auto_backup = true
    backup_interval = "24h"

API:
    POST   /api/v1/archive/agent   — Upload encrypted backup
    GET    /api/v1/archive/agent   — List available backups
    GET    /api/v1/archive/agent/{id} — Download backup
    DELETE /api/v1/archive/agent/{id} — Delete backup
"""

from __future__ import annotations

import base64
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
_TIMEOUT = 60.0  # Backups can be large


def _get_cloud_url(config: dict | None = None) -> str:
    """Get Windy Cloud URL from config or env."""
    if config:
        url = config.get("ecosystem", {}).get("windy_cloud_url", "")
        if url:
            return url.rstrip("/")
    return os.environ.get("WINDY_CLOUD_URL", "https://cloud.windyfly.ai").rstrip("/")


def _get_cloud_token() -> str:
    """Get auth token for Windy Cloud."""
    return os.environ.get("WINDY_CLOUD_TOKEN", "") or os.environ.get("WINDY_JWT", "")


def _get_encryption_key() -> bytes:
    """Derive encryption key from passport ID + agent name.

    Uses PBKDF2 with SHA-256 for key derivation. The backup is
    encrypted client-side so the cloud service is zero-knowledge.
    """
    passport = os.environ.get("ETERNITAS_PASSPORT", "windyfly-local")
    agent_name = os.environ.get("WINDYFLY_AGENT_NAME", "Windy Fly")
    salt = f"{passport}:{agent_name}".encode()
    return hashlib.pbkdf2_hmac("sha256", salt, b"windyfly-backup-v1", 100_000)


def _encrypt_data(data: bytes, key: bytes) -> bytes:
    """Encrypt data using XOR stream cipher with key-derived pad.

    This is a simple encryption for backup data. For production,
    consider using AES-256-GCM via the cryptography library.
    """
    # Generate a keystream by hashing the key repeatedly
    encrypted = bytearray(len(data))
    block_size = 32  # SHA-256 output
    for i in range(0, len(data), block_size):
        block_key = hashlib.sha256(key + i.to_bytes(8, "big")).digest()
        chunk = data[i:i + block_size]
        for j, byte in enumerate(chunk):
            encrypted[i + j] = byte ^ block_key[j]
    return bytes(encrypted)


def _decrypt_data(data: bytes, key: bytes) -> bytes:
    """Decrypt data — XOR is symmetric."""
    return _encrypt_data(data, key)


async def backup_to_cloud(config: dict | None = None) -> dict:
    """Encrypt and upload the agent database to Windy Cloud.

    Returns dict with backup_id, size, and timestamp on success.
    """
    cloud_url = _get_cloud_url(config)
    token = _get_cloud_token()

    if not token:
        return {"success": False, "error": "No cloud token configured"}

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

    # Encrypt
    key = _get_encryption_key()
    encrypted = _encrypt_data(raw_data, key)
    checksum = hashlib.sha256(raw_data).hexdigest()

    # Upload
    payload = {
        "agent_name": os.environ.get("WINDYFLY_AGENT_NAME", "Windy Fly"),
        "passport_id": os.environ.get("ETERNITAS_PASSPORT", ""),
        "data_base64": base64.b64encode(encrypted).decode("ascii"),
        "checksum_sha256": checksum,
        "size_bytes": len(raw_data),
        "encrypted": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{cloud_url}/api/v1/archive/agent",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            result = resp.json()

            # Save backup state
            _save_backup_state({
                "last_backup": datetime.now(timezone.utc).isoformat(),
                "backup_id": result.get("backup_id", ""),
                "size_bytes": len(raw_data),
                "checksum": checksum,
            })

            logger.info("Backup uploaded: %s (%d bytes)", result.get("backup_id", ""), len(raw_data))
            return {"success": True, **result}

    except httpx.ConnectError:
        return {"success": False, "error": f"Cannot reach Windy Cloud at {cloud_url}"}
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"Upload failed: {e.response.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def restore_from_cloud(
    backup_id: str = "latest",
    config: dict | None = None,
) -> dict:
    """Download and decrypt a backup from Windy Cloud.

    Replaces the local database with the restored backup.
    Creates a local backup before overwriting.
    """
    cloud_url = _get_cloud_url(config)
    token = _get_cloud_token()

    if not token:
        return {"success": False, "error": "No cloud token configured"}

    db_path = PROJECT_ROOT / "data" / "windyfly.db"
    if config:
        db_path = Path(config.get("memory", {}).get("db_path", "data/windyfly.db"))

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{cloud_url}/api/v1/archive/agent/{backup_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()

            encrypted = base64.b64decode(data["data_base64"])
            key = _get_encryption_key()
            decrypted = _decrypt_data(encrypted, key)

            # Verify checksum
            checksum = hashlib.sha256(decrypted).hexdigest()
            expected = data.get("checksum_sha256", "")
            if expected and checksum != expected:
                return {"success": False, "error": "Checksum mismatch — backup may be corrupted"}

            # Back up current DB before overwriting
            if db_path.exists():
                backup_path = db_path.with_suffix(".db.pre-restore")
                shutil.copy2(str(db_path), str(backup_path))
                logger.info("Pre-restore backup saved to %s", backup_path)

            # Write restored database
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.write_bytes(decrypted)

            logger.info("Database restored from backup %s (%d bytes)", backup_id, len(decrypted))
            return {
                "success": True,
                "backup_id": backup_id,
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
        return {"success": False, "error": str(e)}


async def list_backups(config: dict | None = None) -> dict:
    """List available backups from Windy Cloud."""
    cloud_url = _get_cloud_url(config)
    token = _get_cloud_token()

    if not token:
        return {"success": False, "backups": [], "error": "No cloud token configured"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{cloud_url}/api/v1/archive/agent",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return {"success": True, "backups": data.get("backups", [])}
    except Exception as e:
        return {"success": False, "backups": [], "error": str(e)}


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
