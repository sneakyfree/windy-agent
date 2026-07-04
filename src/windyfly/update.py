"""Windy Fly update system — check, notify, and apply updates from PyPI.

Update-safety contract (2026-07-04 audit): an update must never leave the
operator worse off than before it ran.

- Every ``apply_update`` records the version it upgraded FROM in
  ``~/.windy/update-history.jsonl`` before touching pip, so ``windy
  rollback`` (no argument) always knows where "back" is.
- After a successful pip install, the new package is import-verified in a
  FRESH interpreter. If the new version can't even import, we auto-roll
  back to the recorded prior version instead of letting the next restart
  brick the agent. This is the anti-OpenClaw invariant: a bad release
  costs the user one failed update message, not a dead agent.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

from windyfly import __version__
from windyfly.platform import windy_state_dir

logger = logging.getLogger(__name__)

PYPI_URL = "https://pypi.org/pypi/windyfly/json"
CHECK_INTERVAL = 86400  # 24 hours
CACHE_FILE = Path("data/.update_check")


def _history_path() -> Path:
    return windy_state_dir() / "update-history.jsonl"


def record_update(from_version: str, to_version: str) -> None:
    """Append an update attempt to the rollback history (best-effort)."""
    try:
        path = _history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": time.time(),
            "from": from_version,
            "to": to_version,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        logger.warning("Could not record update history: %s", e)


def get_previous_version() -> str | None:
    """The version the most recent update upgraded FROM, if recorded."""
    try:
        lines = _history_path().read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return None
        return json.loads(lines[-1]).get("from")
    except (OSError, json.JSONDecodeError):
        return None


def get_latest_version() -> str | None:
    """Fetch latest version from PyPI. Returns None on failure."""
    try:
        resp = httpx.get(PYPI_URL, timeout=5.0)
        if resp.status_code == 200:
            return resp.json()["info"]["version"]
    except Exception as e:
        logger.debug("Version check failed: %s", e)
    return None


_VERSION_RE = re.compile(r"^(\d+(?:\.\d+){0,2})(.*)$")


def is_newer(remote: str, local: str) -> bool:
    """Compare version strings. Returns True if remote > local.

    Handles pre-release suffixes ("1.0.0rc1"): a pre-release sorts BEFORE
    its own final release but after everything below it. Previously any
    suffix raised ValueError and silently reported "not newer". Short
    versions ("1.0") keep their historical tuple-comparison semantics.
    """
    def _parse(v: str) -> tuple[tuple[int, ...], int, str]:
        m = _VERSION_RE.match(v.lstrip("v").strip())
        if not m:
            raise ValueError(f"unparseable version: {v!r}")
        nums = tuple(int(x) for x in m.group(1).split("."))
        suffix = m.group(2)
        # release flag: a final release (no suffix) beats its own
        # pre-releases; suffix string tiebreaks two pre-releases.
        return nums, (1 if not suffix else 0), suffix
    try:
        r_nums, r_final, r_suffix = _parse(remote)
        l_nums, l_final, l_suffix = _parse(local)
    except ValueError:
        return False
    if r_nums != l_nums:
        return r_nums > l_nums
    if r_final != l_final:
        return r_final > l_final
    return r_suffix > l_suffix


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


def _pip_install(pkg: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pkg]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def verify_install() -> tuple[bool, str]:
    """Import-check the installed package in a FRESH interpreter.

    The running process still has the old modules cached; only a new
    interpreter tells us whether the freshly installed version can boot.
    """
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import windyfly, windyfly.config, windyfly.update; "
                "print(windyfly.__version__)",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, "import check timed out"
    if result.returncode != 0:
        return False, result.stderr.strip()[-300:] or "import failed"
    return True, result.stdout.strip()


def apply_update(target_version: str | None = None) -> tuple[bool, str]:
    """Update windyfly via pip, verify, auto-rollback on a broken install.

    Returns (success, message). If target_version is None, upgrades to
    latest.
    """
    prior = __version__
    record_update(prior, target_version or "latest")

    pkg = f"windyfly=={target_version}" if target_version else "windyfly"
    result = _pip_install(pkg)
    if result.returncode != 0:
        return False, f"Update failed: {result.stderr[:300]}"

    ok, detail = verify_install()
    if not ok:
        logger.error(
            "Post-update import check failed (%s) — rolling back to %s",
            detail, prior,
        )
        back = _pip_install(f"windyfly=={prior}")
        if back.returncode == 0:
            return False, (
                f"Update installed but failed verification ({detail}). "
                f"Automatically rolled back to {prior} — you are safe on "
                "your previous version."
            )
        return False, (
            f"Update failed verification ({detail}) AND rollback to "
            f"{prior} failed: {back.stderr[:200]}. "
            f"Run: pip install windyfly=={prior}"
        )

    try:
        CACHE_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    return True, (
        f"Updated to {detail or target_version or 'latest'} (verified). "
        "Restart with 'windy restart'."
    )


def rollback(version: str | None = None) -> tuple[bool, str]:
    """Rollback to a specific version, or the recorded prior version.

    ``windy rollback`` with no argument uses update-history — the
    operator no longer needs to remember what they were running before
    a bad update.
    """
    if version is None:
        version = get_previous_version()
        if not version:
            return False, (
                "No previous version recorded — pass one explicitly: "
                "windy rollback <version>"
            )
    return apply_update(target_version=version)


def get_installed_version() -> str:
    """Get the currently running version."""
    return __version__
