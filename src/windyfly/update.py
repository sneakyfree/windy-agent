"""Windy Fly update system — check, notify, and apply updates from PyPI."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import httpx

from windyfly import __version__

logger = logging.getLogger(__name__)

PYPI_URL = "https://pypi.org/pypi/windyfly/json"
CHECK_INTERVAL = 86400  # 24 hours
CACHE_FILE = Path("data/.update_check")


def get_latest_version() -> str | None:
    """Fetch latest version from PyPI. Returns None on failure."""
    try:
        resp = httpx.get(PYPI_URL, timeout=5.0)
        if resp.status_code == 200:
            return resp.json()["info"]["version"]
    except Exception as e:
        logger.debug("Version check failed: %s", e)
    return None


def is_newer(remote: str, local: str) -> bool:
    """Compare semver strings. Returns True if remote > local."""
    def _parse(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.lstrip("v").split(".")[:3])
    try:
        return _parse(remote) > _parse(local)
    except (ValueError, IndexError):
        return False


def check_for_update(force: bool = False) -> dict | None:
    """Check if update available. Caches result for 24h.

    Returns {"current": "0.5.1", "latest": "0.6.0", "update_available": True}
    or None if no update available.
    """
    if not force and CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text())
            if time.time() - cache.get("checked_at", 0) < CHECK_INTERVAL:
                if cache.get("update_available"):
                    return cache
                return None
        except (json.JSONDecodeError, OSError):
            pass

    latest = get_latest_version()
    if latest is None:
        return None

    result = {
        "current": __version__,
        "latest": latest,
        "update_available": is_newer(latest, __version__),
        "checked_at": time.time(),
    }

    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(result))
    except OSError:
        pass

    return result if result["update_available"] else None


def apply_update(target_version: str | None = None) -> tuple[bool, str]:
    """Update windyfly via pip. Returns (success, message).

    If target_version is None, upgrades to latest.
    """
    pkg = f"windyfly=={target_version}" if target_version else "windyfly"
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pkg]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode == 0:
        try:
            CACHE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return True, f"Updated to {target_version or 'latest'}. Restart with 'windy restart'."

    return False, f"Update failed: {result.stderr[:300]}"


def rollback(version: str) -> tuple[bool, str]:
    """Rollback to a specific version."""
    return apply_update(target_version=version)


def get_installed_version() -> str:
    """Get the currently running version."""
    return __version__
