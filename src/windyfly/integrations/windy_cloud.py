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
    """Backup the agent database to Windy Cloud.

    Returns a mock result when the cloud service is unavailable.
    """
    api_url = os.environ.get("WINDY_CLOUD_URL", "")
    jwt = jwt or os.environ.get("WINDY_JWT", "")

    if not api_url or not jwt:
        return BackupResult(error="Windy Cloud not configured")

    try:
        import httpx
        from pathlib import Path

        db_bytes = Path(db_path).read_bytes()

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{api_url}/api/v1/backup",
                content=db_bytes,
                headers={
                    "Authorization": f"Bearer {jwt}",
                    "X-Encryption-Key": encryption_key,
                    "Content-Type": "application/octet-stream",
                },
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return BackupResult(
                    success=True,
                    backup_id=data.get("backup_id", ""),
                    size_bytes=len(db_bytes),
                )
            return BackupResult(error=f"API returned {resp.status_code}")
    except Exception as exc:
        return BackupResult(error=str(exc))


async def sync_status(jwt: str = "") -> SyncStatus:
    """Check cross-device sync status."""
    api_url = os.environ.get("WINDY_CLOUD_URL", "")
    jwt = jwt or os.environ.get("WINDY_JWT", "")

    if not api_url or not jwt:
        return SyncStatus(error="Windy Cloud not configured")

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_url}/api/v1/sync/status",
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
    except Exception as exc:
        return SyncStatus(error=str(exc))
