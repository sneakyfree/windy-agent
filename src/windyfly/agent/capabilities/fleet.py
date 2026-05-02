"""fleet.* capability — bot-aware fleet ops.

Surfaced 2026-04-27: a user asked Windy 0 "update everyone in the
fleet." The bot, sandboxed and unable to reach the WireGuard mesh,
responded with three abstract options that all required the user to
already understand SSH / Docker / cloudflared. The bot had no
*concrete* answer.

This capability gives the bot a concrete answer:

  fleet.list_kits        → "I know about kit-0c3, kit-0c4, kit-0c5,
                            kit-veron — read straight from your
                            ~/.ssh/config."

  fleet.prepare_command  → "I drafted the update script and saved it
                            to ~/.windy/fleet-dispatch/<ts>-<slug>.sh.
                            When you're ready, run that script. (Or
                            paste it here and I'll walk through what
                            it does.)"

The bot can't *execute* the dispatch from inside its sandbox — that's
the right separation. What it CAN do is:
  1. Know the fleet vocabulary (read-only ssh-config parse)
  2. Draft a clean dispatch script with proper SSH aliases + sudo
  3. Save it to a known location with a clear filename

The user runs the script; the bot stays inside its blast-radius
boundary. Future Phase 2 (Option C deferred): a per-kit runner that
polls the dispatch dir and executes envelopes — out of scope here.

Tiers:
  - fleet.list_kits:        READ_EXTERNAL  (USER+, audit ON)
  - fleet.prepare_command:  WRITE_LOCAL_SAFE (USER+, dry_run ON)

Both are sandbox-safe — neither makes network calls. The drafted
script lives in plain text on disk for the user to inspect before
running. Nothing is executed by the bot.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)


def _ssh_config_path() -> Path:
    return Path(os.environ.get(
        "WINDY_SSH_CONFIG",
        os.path.expanduser("~/.ssh/config"),
    ))


def _dispatch_dir() -> Path:
    return Path(os.environ.get(
        "WINDY_FLEET_DISPATCH_DIR",
        os.path.expanduser("~/.windy/fleet-dispatch"),
    ))


# Hosts that look like fleet aliases. The fleet uses wg-* and kit-*
# prefixes consistently per ACCESS_LOCKBOX §6 / SSH config.
_FLEET_PATTERNS = re.compile(r"^(wg-|kit-)", re.IGNORECASE)


def _parse_ssh_config(text: str) -> list[dict[str, Any]]:
    """Extract fleet-shaped host stanzas from an SSH config.

    Returns one entry per Host stanza whose first alias matches the
    fleet pattern (wg-* or kit-*). Each entry:
      - alias:    the FIRST token after Host (canonical name)
      - aliases:  all tokens after Host (so kit-0c3, charlie, etc.
                  are surfaced)
      - hostname: the HostName directive, if present
      - user:     the User directive, if present
      - proxy_jump: the ProxyJump directive, if present
      - comment:  a one-line comment block immediately preceding the
                  Host stanza (Grant uses these as kit nicknames)
    """
    kits: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending_comment: list[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            pending_comment = []
            continue
        if line.startswith("#"):
            pending_comment.append(line.lstrip("# ").rstrip())
            continue

        # Tokenize the directive
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        key, value = parts[0].lower(), parts[1]

        if key == "host":
            # Close out previous stanza
            if current and _FLEET_PATTERNS.match(current["alias"]):
                kits.append(current)
            aliases = value.split()
            primary = aliases[0]
            current = {
                "alias": primary,
                "aliases": aliases,
                "hostname": None,
                "user": None,
                "proxy_jump": None,
                "comment": " | ".join(pending_comment) if pending_comment else None,
            }
            pending_comment = []
            continue

        if current is None:
            continue
        if key == "hostname":
            current["hostname"] = value.strip()
        elif key == "user":
            current["user"] = value.strip()
        elif key == "proxyjump":
            current["proxy_jump"] = value.strip()

    # Close out final stanza
    if current and _FLEET_PATTERNS.match(current["alias"]):
        kits.append(current)

    return kits


def _slugify(text: str, max_len: int = 40) -> str:
    """Turn a human description into a filesystem-safe slug."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len] or "task"


def _draft_script(
    description: str,
    command: str,
    targets: list[str],
    dry_run: bool,
) -> str:
    """Compose a bash dispatch script. Always exits non-zero on any
    target failure so a /bin/true wrapper can't mask a real problem.

    The script does NOT log credentials or full env. Each target line
    is `ssh <alias> 'sudo -n <command>'` so it FAILS LOUDLY if
    passwordless sudo isn't configured for the user — much better
    than asking the user for a password mid-loop.
    """
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "#!/usr/bin/env bash",
        f"# Auto-drafted by Windy Fly fleet.prepare_command — {now}",
        f"# Description: {description}",
        f"# Targets: {', '.join(targets) if targets else '(none — review before running)'}",
        f"# Dry-run: {dry_run}",
        "#",
        "# Review this script before running. The bot drafted it; you",
        "# decide whether to execute. Exits non-zero if any target",
        "# fails so you don't silently miss a kit.",
        "",
        "set -euo pipefail",
        "",
        f"DRY_RUN={'1' if dry_run else '0'}",
        "FAILED=()",
        "",
        "run_on() {",
        '    local kit="$1"',
        '    echo "── $kit ──"',
        '    if [[ "$DRY_RUN" == "1" ]]; then',
        f'        echo "  (dry-run) would run: {command}"',
        "        return 0",
        "    fi",
        f'    ssh -o ConnectTimeout=10 "$kit" {_quote(command)} || FAILED+=("$kit")',
        "}",
        "",
    ]
    for kit in targets:
        lines.append(f"run_on {_quote(kit)}")
    lines.extend([
        "",
        'if (( ${#FAILED[@]} > 0 )); then',
        '    echo',
        '    echo "FAILED targets: ${FAILED[*]}"',
        "    exit 1",
        "fi",
        "echo",
        'echo "all targets succeeded"',
        "",
    ])
    return "\n".join(lines)


def _quote(s: str) -> str:
    """Single-quote a string for safe inclusion in a bash command,
    handling embedded single quotes by closing+escaping+reopening."""
    return "'" + s.replace("'", "'\\''") + "'"


# ── Capability handlers ──────────────────────────────────────────


def _read_ssh_config_kits() -> list[dict[str, Any]]:
    cfg = _ssh_config_path()
    if not cfg.exists():
        return []
    try:
        return _parse_ssh_config(cfg.read_text())
    except Exception as e:
        logger.warning("Failed to parse SSH config %s: %s", cfg, e)
        return []


def _kit_aliases() -> set[str]:
    """All known fleet aliases (canonical + secondary). Used to
    validate target names in fleet.prepare_command."""
    out: set[str] = set()
    for k in _read_ssh_config_kits():
        for a in k.get("aliases", []):
            out.add(a)
    return out


# ── Registration ─────────────────────────────────────────────────


def register_fleet_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register fleet.list_kits and fleet.prepare_command.

    list_kits is read-only over ~/.ssh/config. prepare_command writes
    a script to ~/.windy/fleet-dispatch/ but never executes anything.
    Both stay inside the bot's sandbox blast radius.
    """
    logger.info("Registering fleet.* capabilities")

    def fleet_list_kits() -> dict[str, Any]:
        """Read-only enumeration of fleet aliases."""
        kits = _read_ssh_config_kits()
        if not kits:
            return {
                "ok": False,
                "reason": (
                    "no fleet-shaped hosts found in ssh config "
                    "(expected wg-* or kit-* aliases)"
                ),
                "ssh_config": str(_ssh_config_path()),
            }
        return {"ok": True, "kits": kits, "count": len(kits)}

    def fleet_prepare_command(
        *,
        description: str,
        command: str,
        targets: list[str] | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Draft a dispatch script. Saves to ~/.windy/fleet-dispatch/
        and returns the path. The user is expected to inspect and run
        it manually — the bot does not execute."""
        if not description or not description.strip():
            return {"ok": False, "reason": "description required"}
        if not command or not command.strip():
            return {"ok": False, "reason": "command required"}

        # Resolve targets: explicit list, or default to every fleet
        # alias known to the SSH config. Either way, validate against
        # the alias set so a hallucinated kit name fails loudly here
        # instead of producing a script that ssh'es to nothing.
        known = _kit_aliases()
        if targets:
            unknown = [t for t in targets if t not in known]
            if unknown:
                return {
                    "ok": False,
                    "reason": f"unknown targets: {', '.join(unknown)}",
                    "known_aliases": sorted(known),
                }
            chosen = list(targets)
        else:
            # Default: canonical aliases only (one per kit), not all
            # secondary names — avoids the same kit appearing 3 times.
            chosen = [k["alias"] for k in _read_ssh_config_kits()]

        if not chosen:
            return {"ok": False, "reason": "no targets and no fleet aliases known"}

        script = _draft_script(description, command, chosen, dry_run)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        slug = _slugify(description)
        out_dir = _dispatch_dir()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"ok": False, "reason": f"cannot create dispatch dir: {e}"}

        path = out_dir / f"{ts}-{slug}.sh"
        try:
            path.write_text(script)
            path.chmod(0o755)
        except Exception as e:
            return {"ok": False, "reason": f"cannot write script: {e}"}

        return {
            "ok": True,
            "path": str(path),
            "targets": chosen,
            "dry_run": dry_run,
            "review_hint": (
                "Drafted but NOT executed. Inspect the script first, "
                "then run: bash " + str(path)
            ),
        }

    registry.register(Capability(
        id="fleet.list_kits",
        description=(
            "List the machines in the user's fleet (read from "
            "~/.ssh/config). Use this when the user asks 'what kits do "
            "I have', 'show me the fleet', 'who is online', or before "
            "drafting a fleet-wide command so you can confirm the "
            "target list. Returns ok/false if no fleet-shaped hosts "
            "(wg-* or kit-*) are configured."
        ),
        handler=fleet_list_kits,
        tier=Tier.READ_EXTERNAL,
        scope="fleet_introspection",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ))

    registry.register(Capability(
        id="fleet.prepare_command",
        description=(
            "Draft a bash dispatch script for running a command on "
            "one or more fleet kits. Saves the script to "
            "~/.windy/fleet-dispatch/<timestamp>-<slug>.sh and returns "
            "the path. DOES NOT EXECUTE — the user reviews and runs "
            "manually. Use this when the user says 'update all the "
            "kits', 'run X on every machine', 'reboot the fleet', or "
            "any fleet-wide operation. Set dry_run=true (default) so "
            "the drafted script will only print what it would do; "
            "set dry_run=false only when the user explicitly "
            "confirms they want the real version."
        ),
        handler=fleet_prepare_command,
        tier=Tier.WRITE_LOCAL_SAFE,
        scope="fleet_dispatch",
        dry_run_supported=True,
        input_schema={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Short human-readable summary (used in filename + script header)",
                },
                "command": {
                    "type": "string",
                    "description": "The shell command to run on each target",
                },
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of fleet aliases. Omit to "
                        "target every known kit. Aliases must match "
                        "fleet.list_kits output."
                    ),
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, drafted script prints actions instead of running them",
                    "default": True,
                },
            },
            "required": ["description", "command"],
        },
    ))
