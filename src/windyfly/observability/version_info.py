"""Live version / uptime / identity introspection.

Backs the /version, /uptime, /whoami Telegram channel handlers so a
user can verify "am I running the latest agent?" without a terminal.

Source-of-truth pulls:
  - SHA / branch / ahead-behind: git CLI in the windy-agent checkout
  - Uptime:                      monotonic clock anchored at import
  - Pause / YOLO / Guest:        existing flag files (no new state)
  - Python / OS:                 stdlib introspection

Design rules:
  - NEVER block on the network. Git is local; flag-file checks are
    one stat() each. /version under 50ms even on a tired iMac.
  - NEVER raise. Each lookup wrapped — a missing .git or unreadable
    flag returns a "?" string, not an exception. The whole point is
    operator confidence; can't have the diagnostic command itself
    crash the bot.
  - PURE READ. No mutation, no LLM call, no DB write.
"""

from __future__ import annotations

import os
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from windyfly import __version__

# Anchor: when this module was first imported. Approximates bot start
# time within ~1s — good enough for grandma-readable uptime.
_PROCESS_START_MONO = time.monotonic()
_PROCESS_START_WALL = datetime.now(timezone.utc)


def _agent_dir() -> Path:
    """Best-effort guess at the windy-agent checkout root.

    Walks up from this file until it finds a .git dir. Falls back to
    the env var WINDY_AGENT_DIR for non-standard installs (e.g.,
    running from a wheel with a sibling checkout)."""
    override = os.environ.get("WINDY_AGENT_DIR")
    if override and (Path(override) / ".git").exists():
        return Path(override)
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists():
            return parent
    return here.parent  # graceful fallback — git lookups will fail soft


def _git(*args: str, cwd: Path | None = None) -> str:
    """Run git, return stripped stdout, "?" on any failure."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(cwd or _agent_dir()),
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "?"


def _uptime_human(seconds: float) -> str:
    """Format like '3d 4h 12m' / '12m 5s' — grandma-readable."""
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if not parts or s:
        parts.append(f"{s}s")
    return " ".join(parts)


def _flag_state() -> dict[str, str]:
    """Snapshot of pause / yolo / guest flag presence. Pure stat()."""
    out = {"pause": "no", "yolo": "no", "guest": "no"}
    try:
        from windyfly.agent.spend_monitor import is_paused, is_yolo_active
        out["pause"] = "yes" if is_paused() else "no"
        out["yolo"] = "yes" if is_yolo_active() else "no"
    except Exception:
        pass
    try:
        from windyfly.agent.guest_mode import is_guest_active
        out["guest"] = "yes" if is_guest_active() else "no"
    except Exception:
        pass
    return out


def get_version_info() -> dict[str, Any]:
    """Full payload for /version. All keys always present.

    When running in a Docker image built by the release pipeline,
    WINDY_BUILD_SHA / WINDY_BUILD_DATE / WINDY_BUILD_VERSION are
    set at image-build time and take precedence over the git
    lookup (which fails inside a container that has no .git dir).
    """
    sha = os.environ.get("WINDY_BUILD_SHA") or _git("rev-parse", "--short", "HEAD")
    sha_full = _git("rev-parse", "HEAD") if not os.environ.get("WINDY_BUILD_SHA") else os.environ["WINDY_BUILD_SHA"]
    branch = _git("symbolic-ref", "--short", "HEAD") or _git("rev-parse", "--abbrev-ref", "HEAD")
    last_commit_when = (
        os.environ.get("WINDY_BUILD_DATE")
        or _git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M:%S")
    )
    last_commit_subject = _git("log", "-1", "--format=%s")

    # Ahead/behind vs origin/<branch>. Best-effort — fails quietly
    # if there's no remote configured or we haven't fetched recently.
    ahead = behind = "?"
    if branch and branch != "?":
        ab = _git("rev-list", "--left-right", "--count", f"origin/{branch}...HEAD")
        if ab and ab != "?":
            try:
                b, a = ab.split()
                behind, ahead = b, a
            except ValueError:
                pass

    # Working-tree dirty?
    dirty_out = _git("status", "--porcelain")
    dirty = bool(dirty_out and dirty_out != "?")

    return {
        "package_version": __version__,
        "sha": sha,
        "sha_full": sha_full,
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "dirty": dirty,
        "last_commit_when": last_commit_when,
        "last_commit_subject": last_commit_subject,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        "os_pretty": _read_os_release(),
        "uptime_seconds": time.monotonic() - _PROCESS_START_MONO,
        "uptime_human": _uptime_human(time.monotonic() - _PROCESS_START_MONO),
        "started_at": _PROCESS_START_WALL.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "flags": _flag_state(),
    }


def _read_os_release() -> str:
    """Pretty OS name from /etc/os-release if available, else
    platform.platform()."""
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return platform.platform()


def format_version_reply() -> str:
    """Markdown reply for /version. Tight, scannable, owner-tone."""
    v = get_version_info()
    dirty_marker = " ⚠ *uncommitted changes*" if v["dirty"] else ""
    branch_state = ""
    if v["ahead"] not in ("?", "0") or v["behind"] not in ("?", "0"):
        branch_state = f" · {v['ahead']} ahead, {v['behind']} behind origin"

    last_commit = ""
    if v["last_commit_subject"] not in (None, "?"):
        subject = v["last_commit_subject"][:80]
        last_commit = f"\n_Latest:_ {subject}"

    flags = v["flags"]
    flag_line = ""
    if any(flags[k] != "no" for k in ("pause", "yolo", "guest")):
        flag_line = (
            f"\n*Flags:* pause={flags['pause']} · "
            f"yolo={flags['yolo']} · guest={flags['guest']}"
        )

    return (
        f"🪰 *Windy Fly* (v{v['package_version']})\n"
        f"*Version:* `{v['sha']}` ({v['last_commit_when']}){dirty_marker}\n"
        f"*Branch:* `{v['branch']}`{branch_state}\n"
        f"*Started:* {v['started_at']}\n"
        f"*Uptime:* {v['uptime_human']}\n"
        f"*Python:* {v['python']} · *OS:* {v['os_pretty']}"
        f"{flag_line}"
        f"{last_commit}"
    )


def format_uptime_reply() -> str:
    """Tight reply for /uptime — just the number + start timestamp."""
    v = get_version_info()
    return (
        f"⏱ *Uptime:* {v['uptime_human']}\n"
        f"_Running since {v['started_at']}_\n"
        f"_Version `{v['sha']}` on branch `{v['branch']}`_"
    )


def format_whoami_reply() -> str:
    """/whoami — friendly identity card. Mixes the static identity
    with live state so it's not just static SOUL output."""
    v = get_version_info()
    name = os.environ.get("WINDY_BOT_NAME", "Windy Fly")
    instance = os.environ.get("WINDY_INSTANCE_ID", "windy-0")
    owner_name = os.environ.get("WINDY_OWNER_NAME", "Grant")
    passport = os.environ.get("ETERNITAS_PASSPORT", "(no passport)")
    return (
        f"🪰 *I'm {name}*\n"
        f"*Instance:* `{instance}`\n"
        f"*Owner:* {owner_name}\n"
        f"*Passport:* `{passport}`\n"
        f"*Version:* `{v['sha']}` · uptime {v['uptime_human']}\n\n"
        f"_I'm an AI companion. Long-term memory and personality "
        f"survive every reset; only my working conversation context "
        f"resets when you /reset or /new._"
    )
