"""Windy Cloud integration — encrypted backup and cross-device sync."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BackupResult:
    """Result of a database backup operation."""

    success: bool = False
    backup_id: str = ""
    size_bytes: int = 0
    error: str = ""


@dataclass
class SyncStatus:
    """Cross-device sync status."""

    is_available: bool = False
    last_sync: str = ""
    devices: int = 0
    error: str = ""


async def backup_database(
    db_path: str, encryption_key: str = "", jwt: str = ""
) -> BackupResult:
    """Backup the agent database to Windy Cloud via R2 storage layer.

    Uses multipart FormData upload to POST /api/storage/files/upload.
    Returns a mock result when the cloud service is unavailable.
    """
    api_url = os.environ.get("WINDY_CLOUD_URL", "")
    jwt = jwt or os.environ.get("WINDY_JWT", "")

    if not api_url or not jwt:
        return BackupResult(error="Windy Cloud not configured")

    try:
        import httpx
        from pathlib import Path

        db_file = Path(db_path)
        if not db_file.exists():
            return BackupResult(error=f"Database file not found: {db_path}")

        db_bytes = db_file.read_bytes()

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{api_url}/api/storage/files/upload",
                files={"file": (db_file.name, db_bytes, "application/octet-stream")},
                data={"encryption_key": encryption_key} if encryption_key else {},
                headers={
                    "Authorization": f"Bearer {jwt}",
                },
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return BackupResult(
                    success=True,
                    backup_id=data.get("backup_id", data.get("file_id", "")),
                    size_bytes=len(db_bytes),
                )
            return BackupResult(error=f"API returned {resp.status_code}")
    except httpx.ConnectError:
        return BackupResult(error="Windy Cloud is not available right now")
    except Exception as exc:
        return BackupResult(error=str(exc))


async def sync_status(jwt: str = "") -> SyncStatus:
    """Check cross-device sync status.

    GET /api/storage/health
    """
    api_url = os.environ.get("WINDY_CLOUD_URL", "")
    jwt = jwt or os.environ.get("WINDY_JWT", "")

    if not api_url or not jwt:
        return SyncStatus(error="Windy Cloud not configured")

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_url}/api/storage/health",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return SyncStatus(
                    is_available=True,
                    last_sync=data.get("last_sync", ""),
                    devices=data.get("devices", 0),
                )
            return SyncStatus(error=f"API returned {resp.status_code}")
    except httpx.ConnectError:
        return SyncStatus(error="Windy Cloud is not available right now")
    except Exception as exc:
        return SyncStatus(error=str(exc))
