"""Windy Fly CLI commands — doctor, update, logs, restart, config, version,
kill, ps, debug, passport, mail, phone, cert, model, soul, budget, memory,
skills, reset, export, import, repl.

All commands built on top of :mod:`windyfly.platform` for cross-platform
compatibility.  Import individual ``cmd_*`` functions from here and register
them in the argparse router in ``cli.py``.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from windyfly.platform import (
    SYSTEM,
    IS_WINDOWS,
    can_run,
    diagnose,
    get_ipc_config,
    get_log_path,
    get_pid_path,
    get_project_root,
    process_alive,
    read_pid_file,
    remove_pid_file,
    force_kill,
)

logger = logging.getLogger(__name__)
console = Console()
PROJECT_ROOT = get_project_root()

# Current version — bump on release
VERSION = "0.5.1"


# ─── Helpers ────────────────────────────────────────────────────────────


def _doc_row(label: str, value: str, ok: bool, hint: str | None = None) -> None:
    """Print a single doctor check row."""
    icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
    line = f"  {icon} [bold]{label}:[/bold] {value}"
    if hint:
        line += f"  [dim]— {hint}[/dim]"
    console.print(line)


def _check_port(port: int) -> bool:
    """Return True if a port is already in use."""
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1)
        s.close()
        return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def _format_uptime(started_iso: str) -> str:
    """Calculate human-readable uptime from an ISO timestamp."""
    try:
        started = datetime.fromisoformat(started_iso)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - started
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            return "just now"
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if not parts:
            parts.append(f"{seconds}s")
        return " ".join(parts)
    except Exception as e:
        logger.debug("Failed to format uptime: %s", e)
        return "unknown"


def _get_db_path() -> Path:
    """Return path to the windyfly database."""
    return PROJECT_ROOT / "data" / "windyfly.db"


def _open_db():
    """Open a sqlite3 connection to windyfly.db, or return None if missing."""
    import sqlite3
    db_path = _get_db_path()
    if not db_path.exists():
        return None
    return sqlite3.connect(str(db_path))


# ═══════════════════════════════════════════════════════════════════════
# windy doctor
# ═══════════════════════════════════════════════════════════════════════


def cmd_doctor(_args: argparse.Namespace) -> None:
    """Run full diagnostics on the Windy Fly installation."""
    console.print()
    console.print(Panel("🪰 [bold cyan]Windy Fly Doctor[/bold cyan]", border_style="cyan"))
    console.print()

    issues: list[str] = []
    warnings: list[str] = []

    # ── 1. Platform & tools ──────────────────────────────────────
    console.print("[bold]Platform & Tools[/bold]")
    report = diagnose()

    _doc_row("OS", f"{report.system}", True)
    _doc_row("Python", report.python_version, sys.version_info >= (3, 12),
             "Need 3.12+" if sys.version_info < (3, 12) else None)
    _doc_row("uv", shutil.which("uv") or "not found", report.has_uv,
             "Install: https://docs.astral.sh/uv/" if not report.has_uv else None)
    _doc_row("Bun", shutil.which("bun") or "not found", report.has_bun,
             "Install: https://bun.sh" if not report.has_bun else None)
    _doc_row("Git", shutil.which("git") or "not found", report.has_git,
             "Install: https://git-scm.com" if not report.has_git else None)

    ipc = get_ipc_config()
    ipc_desc = f"{ipc.mode} — {ipc.socket_path}" if ipc.mode == "uds" else f"{ipc.mode} — {ipc.tcp_host}:{ipc.tcp_port}"
    _doc_row("IPC mode", ipc_desc, True)

    if not report.has_uv:
        issues.append("uv not installed")
    if not report.has_bun:
        issues.append("Bun not installed")
    if sys.version_info < (3, 12):
        issues.append(f"Python {report.python_version} — need 3.12+")

    console.print()

    # ── 2. Configuration files ───────────────────────────────────
    console.print("[bold]Configuration[/bold]")
    env_file = PROJECT_ROOT / ".env"
    toml_file = PROJECT_ROOT / "windyfly.toml"

    _doc_row(".env", str(env_file.relative_to(PROJECT_ROOT)), env_file.exists(),
             "Missing — run `windy init`" if not env_file.exists() else None)
    _doc_row("windyfly.toml", str(toml_file.relative_to(PROJECT_ROOT)), toml_file.exists(),
             "Missing — run `windy init`" if not toml_file.exists() else None)

    soul_file = PROJECT_ROOT / "SOUL.md"
    _doc_row("SOUL.md", str(soul_file.relative_to(PROJECT_ROOT)), soul_file.exists(),
             "Missing — defines agent personality" if not soul_file.exists() else None)

    # Validate windyfly.toml parses correctly
    if toml_file.exists():
        try:
            import tomllib
            with open(toml_file, "rb") as f:
                tomllib.load(f)
            _doc_row("TOML parse", "valid", True)
        except Exception as e:
            _doc_row("TOML parse", f"error: {e}", False,
                     "Fix syntax errors in windyfly.toml")
            issues.append(f"windyfly.toml parse error: {e}")

    if not env_file.exists():
        issues.append(".env missing — run `windy init`")
    if not toml_file.exists():
        issues.append("windyfly.toml missing — run `windy init`")
    if not soul_file.exists():
        warnings.append("SOUL.md missing")

    # Check .env has at least one API key
    if env_file.exists():
        env_content = env_file.read_text()
        key_vars = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY",
                     "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY"]
        has_key = False
        for kv in key_vars:
            for line in env_content.splitlines():
                if line.startswith(f"{kv}=") and len(line.split("=", 1)[1].strip()) > 8:
                    has_key = True
                    break
        _doc_row("API keys", "at least one configured" if has_key else "none found",
                 has_key, "No API keys in .env — run `windy init`" if not has_key else None)
        if not has_key:
            issues.append("No API keys configured")

    console.print()

    # ── 3. Process health ────────────────────────────────────────
    console.print("[bold]Process Health[/bold]")
    pid_file = get_pid_path(PROJECT_ROOT)

    brain_alive = False
    gateway_alive = False
    if pid_file.exists():
        pids = pid_file.read_text().strip().split("\n")
        if len(pids) >= 1:
            try:
                brain_alive = process_alive(int(pids[0].strip()))
            except ValueError:
                pass
        if len(pids) >= 2:
            try:
                gateway_alive = process_alive(int(pids[1].strip()))
            except ValueError:
                pass

    _doc_row("Brain process", "running" if brain_alive else "not running", brain_alive)
    _doc_row("Gateway process", "running" if gateway_alive else "not running", gateway_alive)

    # Check IPC socket/port
    if ipc.mode == "uds":
        sock_exists = os.path.exists(ipc.socket_path)
        _doc_row("IPC socket", ipc.socket_path if sock_exists else "not found",
                 sock_exists)
        if brain_alive and not sock_exists:
            warnings.append("Brain running but IPC socket missing")
    else:
        # TCP — try connecting
        import socket
        tcp_ok = False
        try:
            s = socket.create_connection((ipc.tcp_host, ipc.tcp_port), timeout=2)
            s.close()
            tcp_ok = True
        except (ConnectionRefusedError, OSError, TimeoutError):
            pass
        _doc_row("IPC TCP", f"{ipc.tcp_host}:{ipc.tcp_port}", tcp_ok)

    # Check gateway HTTP
    gateway_http = False
    try:
        import httpx
        r = httpx.get("http://localhost:3000/api/health", timeout=2)
        if r.status_code == 200:
            gateway_http = True
            data = r.json()
            brain_connected = data.get("brain_connected", False)
            _doc_row("Gateway HTTP", "http://localhost:3000", True)
            _doc_row("Brain ↔ Gateway", "connected" if brain_connected else "disconnected",
                     brain_connected,
                     "Gateway running but brain not connected" if not brain_connected else None)
            if not brain_connected:
                warnings.append("Gateway up but brain not connected")
        else:
            _doc_row("Gateway HTTP", f"status {r.status_code}", False)
    except Exception:
        _doc_row("Gateway HTTP", "unreachable", False)

    console.print()

    # ── 4. Port conflicts ────────────────────────────────────────
    console.print("[bold]Port Availability[/bold]")
    for port, name in [(3000, "Gateway"), (4001, "IPC TCP fallback")]:
        if not gateway_http or port != 3000:  # don't flag 3000 if our gateway owns it
            conflict = _check_port(port)
            if conflict and not (port == 3000 and gateway_alive):
                _doc_row(f"Port {port} ({name})", "in use", False,
                         f"Port {port} occupied — may conflict")
                warnings.append(f"Port {port} ({name}) already in use")
            else:
                _doc_row(f"Port {port} ({name})", "available" if not conflict else "ours", True)

    console.print()

    # ── 5. Data directory ────────────────────────────────────────
    console.print("[bold]Data & Storage[/bold]")
    data_dir = PROJECT_ROOT / "data"
    _doc_row("Data directory", str(data_dir.relative_to(PROJECT_ROOT)),
             data_dir.exists())
    db_file = data_dir / "windyfly.db"
    if db_file.exists():
        size_mb = db_file.stat().st_size / (1024 * 1024)
        _doc_row("Database", f"{size_mb:.1f} MB", True)
    else:
        _doc_row("Database", "not created yet", True)

    # Check disk space
    try:
        usage = shutil.disk_usage(str(PROJECT_ROOT))
        free_gb = usage.free / (1024 ** 3)
        low_disk = free_gb < 1.0
        _doc_row("Disk space", f"{free_gb:.1f} GB free", not low_disk,
                 "Less than 1 GB free" if low_disk else None)
        if low_disk:
            warnings.append("Low disk space (<1 GB)")
    except OSError:
        pass

    console.print()

    # ── 6. Dependencies ──────────────────────────────────────────
    console.print("[bold]Dependencies[/bold]")
    lock_file = PROJECT_ROOT / "uv.lock"
    _doc_row("uv.lock", "present" if lock_file.exists() else "missing", lock_file.exists())

    gateway_nm = PROJECT_ROOT / "gateway" / "node_modules"
    _doc_row("gateway/node_modules", "present" if gateway_nm.exists() else "missing",
             gateway_nm.exists(),
             "Run `cd gateway && bun install`" if not gateway_nm.exists() else None)
    if not gateway_nm.exists():
        issues.append("Gateway dependencies not installed")

    console.print()

    # ── 7. External services reachability ─────────────────────────
    console.print("[bold]External Services[/bold]")

    # Eternitas API
    eternitas_url = os.environ.get("ETERNITAS_API_URL", "")
    if eternitas_url:
        try:
            import httpx
            r = httpx.get(f"{eternitas_url}/health", timeout=3)
            reachable = r.status_code < 500
            _doc_row("Eternitas API", eternitas_url, reachable,
                     "API returned error" if not reachable else None)
        except Exception:
            _doc_row("Eternitas API", eternitas_url, False,
                     "Unreachable — check ETERNITAS_API_URL")
            warnings.append("Eternitas API unreachable")
    else:
        _doc_row("Eternitas API", "not configured", True, "Set ETERNITAS_API_URL to enable")

    # Windy Pro API
    windy_api_url = os.environ.get("WINDY_API_URL", "")
    if not windy_api_url:
        try:
            from windyfly.config import load_config
            _cfg = load_config()
            windy_api_url = _cfg.get("windy_api", {}).get("base_url", "")
        except Exception as e:
            logger.debug("Failed to load windy_api config: %s", e)
    if windy_api_url:
        try:
            import httpx
            r = httpx.get(f"{windy_api_url}/health", timeout=3)
            reachable = r.status_code < 500
            _doc_row("Windy Pro API", windy_api_url, reachable,
                     "API returned error" if not reachable else None)
        except Exception:
            _doc_row("Windy Pro API", windy_api_url, False,
                     "Unreachable — is Windy Pro running?")
            warnings.append("Windy Pro API unreachable")
    else:
        _doc_row("Windy Pro API", "not configured", True)

    # Matrix homeserver
    matrix_hs = os.environ.get("MATRIX_HOMESERVER", "")
    if not matrix_hs:
        try:
            from windyfly.config import load_config
            _cfg = load_config()
            matrix_hs = _cfg.get("matrix", {}).get("homeserver", "")
        except Exception as e:
            logger.debug("Failed to load matrix config: %s", e)
    if matrix_hs:
        try:
            import httpx
            r = httpx.get(f"{matrix_hs}/_matrix/client/versions", timeout=3)
            reachable = r.status_code == 200
            _doc_row("Matrix homeserver", matrix_hs, reachable,
                     "Unreachable — check homeserver URL" if not reachable else None)
        except Exception:
            _doc_row("Matrix homeserver", matrix_hs, False,
                     "Unreachable — check network and URL")
            warnings.append("Matrix homeserver unreachable")
    else:
        _doc_row("Matrix homeserver", "not configured", True)

    # Windy Mail
    mail_url = os.environ.get("WINDYMAIL_API_URL", "")
    if mail_url:
        try:
            import httpx
            r = httpx.get(f"{mail_url}/health", timeout=3)
            reachable = r.status_code < 500
            _doc_row("Windy Mail", mail_url, reachable,
                     "API returned error" if not reachable else None)
        except Exception:
            _doc_row("Windy Mail", mail_url, False,
                     "Unreachable — check WINDYMAIL_API_URL")
            warnings.append("Windy Mail unreachable")
    else:
        _doc_row("Windy Mail", "not configured", True)

    # Windy Cloud
    cloud_url = os.environ.get("WINDY_CLOUD_URL", "")
    if cloud_url:
        try:
            import httpx
            r = httpx.get(f"{cloud_url}/api/storage/health", timeout=3)
            reachable = r.status_code < 500
            _doc_row("Windy Cloud", cloud_url, reachable,
                     "API returned error" if not reachable else None)
        except Exception:
            _doc_row("Windy Cloud", cloud_url, False,
                     "Unreachable — check WINDY_CLOUD_URL")
            warnings.append("Windy Cloud unreachable")
    else:
        _doc_row("Windy Cloud", "not configured", True)

    console.print()

    # ── 8. Provisioning recovery ─────────────────────────────────
    recovery_file = PROJECT_ROOT / "data" / "provision_recovery.json"
    if recovery_file.exists():
        console.print("[bold]Provisioning Recovery[/bold]")
        try:
            import json
            recovery = json.loads(recovery_file.read_text())
            failed = recovery.get("failed_steps", [])
            retries = recovery.get("retry_count", 0)
            _doc_row("Failed steps", ", ".join(failed), False,
                     f"Retry count: {retries}")
            warnings.append(f"Provisioning recovery pending: {', '.join(failed)}")
            console.print("  [dim]Run [bold]windy ecosystem[/bold] to retry provisioning.[/dim]")
        except Exception:
            _doc_row("Recovery file", "corrupt", False, "Delete data/provision_recovery.json")
        console.print()

    # ── Summary ──────────────────────────────────────────────────
    if not issues and not warnings:
        console.print(Panel(
            "[bold green]All checks passed![/bold green]\n"
            "[dim]Your Windy Fly installation looks healthy.[/dim]",
            border_style="green",
        ))
    else:
        lines = []
        for issue in issues:
            lines.append(f"  [red]✗[/red] {issue}")
        for warn in warnings:
            lines.append(f"  [yellow]⚠[/yellow] {warn}")
        severity = "red" if issues else "yellow"
        header = f"[bold {severity}]{len(issues)} issue(s), {len(warnings)} warning(s)[/bold {severity}]"
        console.print(Panel(
            header + "\n" + "\n".join(lines),
            border_style=severity,
        ))

    if issues:
        console.print()
        console.print("[dim]  Fix the issues above, then run [bold]windy doctor[/bold] again.[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# windy update
# ═══════════════════════════════════════════════════════════════════════


def cmd_update(_args: argparse.Namespace) -> None:
    """Update Windy Fly — git pull + uv sync + bun install."""
    console.print()
    console.print("[bold cyan]🪰 Updating Windy Fly...[/bold cyan]")
    console.print()

    # Check if it's a git repo
    git_dir = PROJECT_ROOT / ".git"
    if not git_dir.exists():
        console.print("[yellow]⚠ Not a git repository — skipping git pull.[/yellow]")
        console.print("[dim]  If you installed manually, pull the latest code yourself.[/dim]")
    else:
        # Save current commit for rollback reference
        try:
            old_commit = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Failed to get current git commit: %s", e)
            old_commit = "unknown"

        console.print("  [cyan]Pulling latest code...[/cyan]")
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True,
        )
        if result.returncode == 0:
            try:
                new_commit = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=str(PROJECT_ROOT), capture_output=True, text=True,
                ).stdout.strip()
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("Failed to get new git commit: %s", e)
                new_commit = "unknown"

            if old_commit == new_commit:
                console.print(f"  [green]✓[/green] Already up to date [dim]({new_commit})[/dim]")
            else:
                console.print(f"  [green]✓[/green] Updated [dim]{old_commit} → {new_commit}[/dim]")
        else:
            console.print(f"  [yellow]⚠ git pull failed:[/yellow] {result.stderr.strip()}")
            console.print("  [dim]You may have local changes. Try: git stash && windy update[/dim]")

    console.print()

    # Python deps
    console.print("  [cyan]Syncing Python dependencies...[/cyan]")
    result = subprocess.run(
        ["uv", "sync"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True,
    )
    if result.returncode == 0:
        console.print("  [green]✓[/green] Python dependencies synced")
    else:
        console.print(f"  [red]✗ uv sync failed:[/red] {result.stderr.strip()[:200]}")
        console.print("  [dim]Try running manually: uv sync[/dim]")

    # Gateway deps
    gateway_dir = PROJECT_ROOT / "gateway"
    if gateway_dir.exists():
        console.print("  [cyan]Syncing gateway dependencies...[/cyan]")
        result = subprocess.run(
            ["bun", "install"],
            cwd=str(gateway_dir), capture_output=True, text=True,
        )
        if result.returncode == 0:
            console.print("  [green]✓[/green] Gateway dependencies synced")
        else:
            console.print(f"  [red]✗ bun install failed:[/red] {result.stderr.strip()[:200]}")

    console.print()
    console.print("[bold green]🪰 Update complete![/bold green]")
    console.print()

    # Check if processes are running — suggest restart
    pid_file = get_pid_path(PROJECT_ROOT)
    if pid_file.exists():
        pids = pid_file.read_text().strip().split("\n")
        alive = any(process_alive(int(p.strip())) for p in pids if p.strip().isdigit())
        if alive:
            console.print("  [yellow]⚠ Windy Fly is running — restart to pick up changes:[/yellow]")
            console.print("    [bold]windy restart[/bold]")
            console.print()


# ═══════════════════════════════════════════════════════════════════════
# windy logs
# ═══════════════════════════════════════════════════════════════════════


def cmd_logs(args: argparse.Namespace) -> None:
    """Tail brain and/or gateway logs."""
    component = getattr(args, "component", "all")
    follow = getattr(args, "follow", False)
    lines = getattr(args, "lines", 50)

    brain_log = get_log_path(PROJECT_ROOT, "brain")
    gateway_log = get_log_path(PROJECT_ROOT, "gateway")

    targets: list[tuple[str, Path]] = []
    if component in ("all", "brain"):
        targets.append(("Brain", brain_log))
    if component in ("all", "gateway"):
        targets.append(("Gateway", gateway_log))

    # Check if any logs exist
    existing = [(name, path) for name, path in targets if path.exists()]
    if not existing:
        console.print("[dim]No log files found. Start Windy Fly first: [bold]windy start[/bold][/dim]")
        return

    if follow:
        # Follow mode — use tail -f (POSIX) or Get-Content -Wait (Windows)
        console.print("[dim]Following logs... Press Ctrl+C to stop.[/dim]")
        console.print()
        paths = [str(p) for _, p in existing]
        try:
            if IS_WINDOWS:
                # PowerShell can tail multiple files
                ps_cmd = " ; ".join(
                    f"Get-Content '{p}' -Tail {lines} -Wait"
                    for p in paths
                )
                subprocess.run(["powershell", "-Command", ps_cmd])
            else:
                subprocess.run(["tail", "-f", "-n", str(lines)] + paths)
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped following logs.[/dim]")
    else:
        # Static mode — read last N lines
        for name, path in existing:
            console.print(f"[bold cyan]── {name} ──[/bold cyan] [dim]{path}[/dim]")
            try:
                content = path.read_text()
                log_lines = content.strip().splitlines()
                tail = log_lines[-lines:] if len(log_lines) > lines else log_lines
                for line in tail:
                    # Basic colorization
                    if "error" in line.lower() or "ERROR" in line:
                        console.print(f"  [red]{line}[/red]")
                    elif "warn" in line.lower() or "WARNING" in line:
                        console.print(f"  [yellow]{line}[/yellow]")
                    else:
                        console.print(f"  [dim]{line}[/dim]")
            except Exception as e:
                console.print(f"  [red]Could not read: {e}[/red]")
            console.print()


# ═══════════════════════════════════════════════════════════════════════
# windy restart
# ═══════════════════════════════════════════════════════════════════════


def cmd_restart(args: argparse.Namespace) -> None:
    """Stop then start Windy Fly."""
    from windyfly.cli import cmd_stop, cmd_start

    console.print("[bold cyan]🪰 Restarting Windy Fly...[/bold cyan]")
    console.print()

    # Stop
    cmd_stop(args)
    console.print()

    # Brief pause for ports/sockets to release
    time.sleep(1)

    # Start — need to set cli=False if not present
    if not hasattr(args, "cli"):
        args.cli = False
    cmd_start(args)


# ═══════════════════════════════════════════════════════════════════════
# windy config
# ═══════════════════════════════════════════════════════════════════════


def cmd_config(args: argparse.Namespace) -> None:
    """View or edit Windy Fly configuration."""
    action = getattr(args, "action", "show")

    if action == "show":
        _config_show()
    elif action == "set":
        key = getattr(args, "key", "")
        value = getattr(args, "value", "")
        _config_set(key, value)
    elif action == "reset":
        _config_reset()
    elif action == "path":
        _config_path()


def _config_show() -> None:
    """Display the current configuration."""
    toml_file = PROJECT_ROOT / "windyfly.toml"
    env_file = PROJECT_ROOT / ".env"

    if not toml_file.exists():
        console.print("[yellow]No windyfly.toml found. Run [bold]windy init[/bold] first.[/yellow]")
        return

    import tomllib
    with open(toml_file, "rb") as f:
        config = tomllib.load(f)

    table = Table(title="🪰 Windy Fly Configuration", border_style="cyan", show_lines=True)
    table.add_column("Section", style="bold cyan")
    table.add_column("Key", style="bold")
    table.add_column("Value", style="green")

    for section, values in config.items():
        if isinstance(values, dict):
            for key, val in values.items():
                table.add_row(section, key, str(val))
        else:
            table.add_row("", section, str(values))

    console.print(table)
    console.print()

    # Show which API keys are configured (masked)
    if env_file.exists():
        console.print("[bold]API Keys[/bold]")
        key_vars = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY",
                     "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY"]
        env_content = env_file.read_text()
        for kv in key_vars:
            for line in env_content.splitlines():
                if line.startswith(f"{kv}="):
                    val = line.split("=", 1)[1].strip()
                    if val and len(val) > 8:
                        masked = val[:6] + "..." + val[-4:]
                        console.print(f"  [green]✓[/green] {kv}: [dim]{masked}[/dim]")
                    else:
                        console.print(f"  [dim]–[/dim] {kv}: [dim]not set[/dim]")
                    break
        console.print()


def _config_set(key: str, value: str) -> None:
    """Set a configuration value in windyfly.toml."""
    toml_file = PROJECT_ROOT / "windyfly.toml"
    if not toml_file.exists():
        console.print("[yellow]No windyfly.toml found. Run [bold]windy init[/bold] first.[/yellow]")
        return

    # Parse section.key format
    parts = key.split(".", 1)
    if len(parts) != 2:
        console.print("[red]Key must be in section.key format (e.g., agent.default_model)[/red]")
        return

    section, config_key = parts

    # Read, modify, write
    content = toml_file.read_text()
    lines = content.splitlines()
    in_section = False
    found = False
    new_lines = []

    for line in lines:
        stripped = line.strip()
        # Track which section we're in
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == f"[{section}]"

        if in_section and (stripped.startswith(f"{config_key} =") or
                           stripped.startswith(f"{config_key}=")):
            # Try to preserve the value type
            try:
                int(value)
                new_lines.append(f"{config_key} = {value}")
            except ValueError:
                try:
                    float(value)
                    new_lines.append(f"{config_key} = {value}")
                except ValueError:
                    if value.lower() in ("true", "false"):
                        new_lines.append(f"{config_key} = {value.lower()}")
                    else:
                        new_lines.append(f'{config_key} = "{value}"')
            found = True
            continue

        new_lines.append(line)

    if found:
        toml_file.write_text("\n".join(new_lines) + "\n")
        console.print(f"  [green]✓[/green] Set [bold]{section}.{config_key}[/bold] = {value}")
    else:
        console.print(f"  [red]✗[/red] Key [bold]{key}[/bold] not found in windyfly.toml")
        console.print("  [dim]Run [bold]windy config show[/bold] to see available keys.[/dim]")


def _config_reset() -> None:
    """Reset configuration by re-running the setup wizard."""
    console.print("[cyan]Re-running setup wizard to reset configuration...[/cyan]")
    console.print()
    from windyfly.setup_wizard import run_wizard
    run_wizard()


def _config_path() -> None:
    """Show paths to all config files."""
    console.print(f"  [bold]Config:[/bold]  {PROJECT_ROOT / 'windyfly.toml'}")
    console.print(f"  [bold]Env:[/bold]     {PROJECT_ROOT / '.env'}")
    console.print(f"  [bold]Soul:[/bold]    {PROJECT_ROOT / 'SOUL.md'}")
    console.print(f"  [bold]Data:[/bold]    {PROJECT_ROOT / 'data'}")
    console.print(f"  [bold]Logs:[/bold]    {get_log_path(PROJECT_ROOT, 'brain')}")
    console.print(f"           {get_log_path(PROJECT_ROOT, 'gateway')}")


# ═══════════════════════════════════════════════════════════════════════
# windy version
# ═══════════════════════════════════════════════════════════════════════


def cmd_version(_args: argparse.Namespace) -> None:
    """Show Windy Fly version and environment info."""
    ipc = get_ipc_config()

    console.print()
    console.print(f"  [bold cyan]🪰 Windy Fly[/bold cyan]  v{VERSION}")
    console.print()
    console.print(f"  [bold]Platform:[/bold]   {SYSTEM}")
    console.print(f"  [bold]Python:[/bold]     {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    # uv version
    if can_run("uv"):
        try:
            uv_ver = subprocess.run(
                ["uv", "--version"], capture_output=True, text=True,
            ).stdout.strip()
            console.print(f"  [bold]uv:[/bold]         {uv_ver}")
        except (subprocess.SubprocessError, OSError):
            console.print("  [bold]uv:[/bold]         installed")
    else:
        console.print("  [bold]uv:[/bold]         [red]not found[/red]")

    # Bun version
    if can_run("bun"):
        try:
            bun_ver = subprocess.run(
                ["bun", "--version"], capture_output=True, text=True,
            ).stdout.strip()
            console.print(f"  [bold]Bun:[/bold]        {bun_ver}")
        except (subprocess.SubprocessError, OSError):
            console.print("  [bold]Bun:[/bold]        installed")
    else:
        console.print("  [bold]Bun:[/bold]        [red]not found[/red]")

    ipc_desc = ipc.socket_path if ipc.mode == "uds" else f"{ipc.tcp_host}:{ipc.tcp_port}"
    console.print(f"  [bold]IPC:[/bold]        {ipc.mode} ({ipc_desc})")

    # Git info
    if can_run("git"):
        try:
            commit = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            ).stdout.strip()
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            ).stdout.strip()
            console.print(f"  [bold]Git:[/bold]        {branch} @ {commit}")
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Failed to get git info for version: %s", e)

    console.print(f"  [bold]Root:[/bold]       {PROJECT_ROOT}")
    console.print()


# ═══════════════════════════════════════════════════════════════════════
# windy kill
# ═══════════════════════════════════════════════════════════════════════


def cmd_kill(_args: argparse.Namespace) -> None:
    """Force-kill all Windy Fly processes."""
    from windyfly.platform import kill_by_name

    project_root = PROJECT_ROOT
    info = read_pid_file(project_root)
    killed = 0

    # Kill known PIDs first
    if info:
        for pid in [info.brain, info.gateway]:
            if pid and process_alive(pid):
                force_kill(pid)
                killed += 1

    # Fallback: kill by name pattern
    kill_by_name(["windyfly", "windy.fly", "windyfly.bridge", "windyfly.main"])

    # Clean up files
    remove_pid_file(project_root)

    # Remove stale socket
    ipc = get_ipc_config()
    if ipc.mode == "uds" and os.path.exists(ipc.socket_path):
        os.unlink(ipc.socket_path)

    console.print("[bold green]All Windy Fly processes force-killed.[/bold green]")


# ═══════════════════════════════════════════════════════════════════════
# windy ps
# ═══════════════════════════════════════════════════════════════════════


def cmd_ps(_args: argparse.Namespace) -> None:
    """Show all running Windy Fly processes."""
    info = read_pid_file(PROJECT_ROOT)

    table = Table(title="Windy Fly Processes", border_style="cyan")
    table.add_column("PID", style="bold")
    table.add_column("Component", style="bold cyan")
    table.add_column("Status")
    table.add_column("Uptime")

    has_any = False
    if info:
        started = info.started  # ISO timestamp
        for label, pid in [("Brain", info.brain), ("Gateway", info.gateway)]:
            if pid:
                alive = process_alive(pid)
                status = "[green]Running[/green]" if alive else "[red]Dead[/red]"
                uptime = _format_uptime(started) if alive and started else "—"
                table.add_row(str(pid), label, status, uptime)
                has_any = True

    if has_any:
        console.print(table)
    else:
        console.print("[dim]No Windy Fly processes running.[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# windy debug
# ═══════════════════════════════════════════════════════════════════════


def cmd_debug(_args: argparse.Namespace) -> None:
    """Verbose diagnostic info for bug reports."""
    import platform as plat

    lines: list[str] = []
    lines.append("=== Windy Fly Debug Report ===")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    # System info
    lines.append("--- System ---")
    lines.append(f"Python:       {sys.version}")
    lines.append(f"OS:           {plat.platform()}")
    lines.append(f"Architecture: {plat.machine()}")
    lines.append(f"Windy Fly:    v{VERSION}")
    lines.append("")

    # Package versions
    lines.append("--- Package Versions ---")
    key_packages = [
        "openai", "anthropic", "httpx", "pydantic", "rich", "matrix-nio",
        "uvicorn", "fastapi", "tomli", "tomllib", "dotenv",
    ]
    for pkg_name in key_packages:
        try:
            from importlib.metadata import version as pkg_version
            ver = pkg_version(pkg_name)
            lines.append(f"  {pkg_name}: {ver}")
        except Exception:
            lines.append(f"  {pkg_name}: not installed")
    lines.append("")

    # .env contents (REDACTED)
    lines.append("--- .env (REDACTED) ---")
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(f"  {stripped}")
                continue
            if "=" in stripped:
                env_key = stripped.split("=", 1)[0]
                lines.append(f"  {env_key}=****")
            else:
                lines.append(f"  {stripped}")
    else:
        lines.append("  .env not found")
    lines.append("")

    # Last 20 log lines
    lines.append("--- Last 20 Brain Log Lines ---")
    brain_log = get_log_path(PROJECT_ROOT, "brain")
    if brain_log.exists():
        try:
            log_content = brain_log.read_text().strip().splitlines()
            tail = log_content[-20:] if len(log_content) > 20 else log_content
            for log_line in tail:
                lines.append(f"  {log_line}")
        except Exception as e:
            lines.append(f"  Error reading log: {e}")
    else:
        lines.append("  brain.log not found")
    lines.append("")

    # Database stats
    lines.append("--- Database ---")
    db_path = _get_db_path()
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        lines.append(f"  Size: {size_mb:.2f} MB")
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row[0] for row in cursor.fetchall()]
            lines.append(f"  Tables ({len(tables)}): {', '.join(tables)}")
            for tbl in tables:
                try:
                    count = conn.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]
                    lines.append(f"    {tbl}: {count} rows")
                except Exception:
                    lines.append(f"    {tbl}: error reading")
            conn.close()
        except Exception as e:
            lines.append(f"  Error reading DB: {e}")
    else:
        lines.append("  Database not found")
    lines.append("")

    # Git commit hash
    lines.append("--- Git ---")
    if can_run("git"):
        try:
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            ).stdout.strip()
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            ).stdout.strip()
            lines.append(f"  Branch: {branch}")
            lines.append(f"  Commit: {commit}")
        except (subprocess.SubprocessError, OSError):
            lines.append("  Error reading git info")
    else:
        lines.append("  git not available")
    lines.append("")
    lines.append("=== End Debug Report ===")

    output = "\n".join(lines)
    console.print(Panel(output, title="Debug Report", border_style="dim", expand=False))
    console.print()
    console.print("[dim]Copy the text above and include it in your bug report.[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# windy passport
# ═══════════════════════════════════════════════════════════════════════


def cmd_passport(_args: argparse.Namespace) -> None:
    """Show Eternitas passport info."""
    passport_id = os.environ.get("ETERNITAS_PASSPORT", "")
    if not passport_id:
        console.print("[dim]No passport. Run [bold]windy go[/bold] to register.[/dim]")
        return

    console.print(f"  [bold]Passport ID:[/bold]  {passport_id}")

    # Try to look up details from Eternitas
    try:
        import httpx
        url = os.environ.get("ETERNITAS_API_URL", "")
        if url:
            r = httpx.get(f"{url}/api/v1/registry/verify/{passport_id}", timeout=5)
            if r.status_code == 200:
                data = r.json()
                console.print(f"  [bold]Status:[/bold]       {data.get('status', 'unknown')}")
                console.print(f"  [bold]Trust:[/bold]        {data.get('trust_score', '?')}/100")
                console.print(f"  [bold]Created:[/bold]      {data.get('created_at', 'unknown')}")
    except Exception as e:
        logger.debug("Failed to look up passport details: %s", e)


# ═══════════════════════════════════════════════════════════════════════
# windy mail
# ═══════════════════════════════════════════════════════════════════════


def cmd_mail(_args: argparse.Namespace) -> None:
    """Show mail status."""
    email = None

    # Check environment variables first
    email = os.environ.get("WINDYFLY_EMAIL_ADDRESS", "") or \
            os.environ.get("WINDYMAIL_EMAIL", "")

    # Try DB soul table
    if not email:
        conn = _open_db()
        if conn:
            try:
                cursor = conn.execute(
                    "SELECT value FROM soul WHERE key = 'email_address'"
                )
                row = cursor.fetchone()
                if row:
                    email = row[0]
            except Exception as e:
                logger.debug("Failed to query soul table for email: %s", e)
            finally:
                conn.close()

    if email:
        console.print(f"  [bold]Email:[/bold]  {email}")

        # Check Windy Mail API status
        mail_url = os.environ.get("WINDYMAIL_API_URL", "")
        if mail_url:
            try:
                import httpx
                r = httpx.get(f"{mail_url}/health", timeout=3)
                status = "[green]Connected[/green]" if r.status_code < 500 else "[red]Error[/red]"
                console.print(f"  [bold]Mail API:[/bold]  {status}")
            except Exception:
                console.print("  [bold]Mail API:[/bold]  [red]Unreachable[/red]")
        else:
            console.print("  [bold]Mail API:[/bold]  [dim]Not configured[/dim]")
    else:
        console.print("[dim]No email configured. Set WINDYFLY_EMAIL_ADDRESS or run [bold]windy go[/bold].[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# windy phone
# ═══════════════════════════════════════════════════════════════════════


def cmd_phone(_args: argparse.Namespace) -> None:
    """Show phone status."""
    phone = os.environ.get("TWILIO_PHONE_NUMBER", "") or \
            os.environ.get("AGENT_PHONE", "")

    if phone:
        console.print(f"  [bold]Phone:[/bold]  {phone}")

        # Check if Twilio credentials are present
        twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        twilio_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        if twilio_sid and twilio_token:
            console.print("  [bold]Twilio:[/bold]  [green]Configured[/green]")
        else:
            console.print("  [bold]Twilio:[/bold]  [yellow]Credentials missing[/yellow]")
    else:
        console.print("[dim]No phone configured. Set TWILIO_PHONE_NUMBER or AGENT_PHONE.[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# windy cert
# ═══════════════════════════════════════════════════════════════════════


def cmd_cert(_args: argparse.Namespace) -> None:
    """Show/open birth certificate."""
    import glob

    certs = sorted(glob.glob(str(PROJECT_ROOT / "data" / "birth_certificate_*.pdf")))
    if not certs:
        console.print("[dim]No certificate. Run [bold]windy go[/bold] to generate.[/dim]")
        return

    cert = certs[-1]  # latest
    console.print(f"  [bold]Certificate:[/bold]  {cert}")

    # Try to open on macOS
    if sys.platform == "darwin":
        subprocess.run(["open", cert], capture_output=True)
        console.print("  [dim]Opening PDF...[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# windy model
# ═══════════════════════════════════════════════════════════════════════


def cmd_model(args: argparse.Namespace) -> None:
    """Model management — show, list, set, test."""
    action = getattr(args, "action", None)

    if action is None:
        _model_show()
    elif action == "list":
        _model_list()
    elif action == "set":
        model_name = getattr(args, "model", "")
        _model_set(model_name)
    elif action == "test":
        _model_test()


def _model_show() -> None:
    """Show the current default model."""
    model = os.environ.get("DEFAULT_MODEL", "")

    if not model:
        # Try windyfly.toml
        toml_file = PROJECT_ROOT / "windyfly.toml"
        if toml_file.exists():
            try:
                import tomllib
                with open(toml_file, "rb") as f:
                    config = tomllib.load(f)
                model = config.get("agent", {}).get("default_model", "")
            except Exception as e:
                logger.debug("Failed to load model config: %s", e)

    if model:
        console.print(f"  [bold]Current model:[/bold]  {model}")
    else:
        console.print("[dim]No model configured. Run [bold]windy model set <model>[/bold].[/dim]")


def _model_list() -> None:
    """List all available models grouped by provider."""
    models = {
        "OpenAI": [
            "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4",
            "o1", "o1-mini", "o1-pro", "o3-mini",
        ],
        "Anthropic": [
            "claude-opus-4-0", "claude-sonnet-4-0",
            "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
        ],
        "Google": [
            "gemini-2.0-flash", "gemini-2.0-pro", "gemini-1.5-pro", "gemini-1.5-flash",
        ],
        "xAI": [
            "grok-2", "grok-2-mini",
        ],
        "DeepSeek": [
            "deepseek-chat", "deepseek-reasoner",
        ],
        "Mistral": [
            "mistral-large-latest", "mistral-medium-latest", "mistral-small-latest",
        ],
    }

    current = os.environ.get("DEFAULT_MODEL", "")

    table = Table(title="Available Models", border_style="cyan")
    table.add_column("Provider", style="bold cyan")
    table.add_column("Model", style="bold")
    table.add_column("", style="dim")

    for provider, model_list in models.items():
        for i, model in enumerate(model_list):
            prov = provider if i == 0 else ""
            marker = "[green]<-- current[/green]" if model == current else ""
            table.add_row(prov, model, marker)

    console.print(table)


def _model_set(model_name: str) -> None:
    """Set the default model in .env."""
    if not model_name:
        console.print("[red]Usage: windy model set <model-name>[/red]")
        return

    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        content = env_file.read_text()
        lines = content.splitlines()
        found = False
        new_lines = []
        for line in lines:
            if line.startswith("DEFAULT_MODEL="):
                new_lines.append(f"DEFAULT_MODEL={model_name}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"DEFAULT_MODEL={model_name}")
        env_file.write_text("\n".join(new_lines) + "\n")
    else:
        env_file.write_text(f"DEFAULT_MODEL={model_name}\n")

    os.environ["DEFAULT_MODEL"] = model_name
    console.print(f"  [green]✓[/green] Default model set to [bold]{model_name}[/bold]")


def _model_test() -> None:
    """Send a test message to the current model."""
    model = os.environ.get("DEFAULT_MODEL", "")
    if not model:
        console.print("[yellow]No model configured. Run [bold]windy model set <model>[/bold] first.[/yellow]")
        return

    console.print(f"  [cyan]Testing model [bold]{model}[/bold]...[/cyan]")

    try:
        # Determine provider from model name
        if model.startswith("gpt-") or model.startswith("o1") or model.startswith("o3"):
            import openai
            client = openai.OpenAI()
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say hello in one sentence."}],
                max_tokens=50,
            )
            reply = response.choices[0].message.content
        elif model.startswith("claude"):
            import anthropic
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=model,
                max_tokens=50,
                messages=[{"role": "user", "content": "Say hello in one sentence."}],
            )
            reply = response.content[0].text
        elif model.startswith("gemini"):
            import httpx
            api_key = os.environ.get("GEMINI_API_KEY", "")
            r = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                json={"contents": [{"parts": [{"text": "Say hello in one sentence."}]}]},
                timeout=15,
            )
            reply = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        elif model.startswith("grok"):
            import openai
            client = openai.OpenAI(
                api_key=os.environ.get("GROK_API_KEY", ""),
                base_url="https://api.x.ai/v1",
            )
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say hello in one sentence."}],
                max_tokens=50,
            )
            reply = response.choices[0].message.content
        elif model.startswith("deepseek"):
            import openai
            client = openai.OpenAI(
                api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
                base_url="https://api.deepseek.com",
            )
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say hello in one sentence."}],
                max_tokens=50,
            )
            reply = response.choices[0].message.content
        elif model.startswith("mistral"):
            import openai
            client = openai.OpenAI(
                api_key=os.environ.get("MISTRAL_API_KEY", ""),
                base_url="https://api.mistral.ai/v1",
            )
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say hello in one sentence."}],
                max_tokens=50,
            )
            reply = response.choices[0].message.content
        else:
            console.print(f"  [yellow]Unknown provider for model '{model}'. Trying OpenAI-compatible...[/yellow]")
            import openai
            client = openai.OpenAI()
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say hello in one sentence."}],
                max_tokens=50,
            )
            reply = response.choices[0].message.content

        console.print(f"  [green]✓[/green] Response: [italic]{reply}[/italic]")

    except Exception as e:
        console.print(f"  [red]✗ Model test failed:[/red] {e}")


# ═══════════════════════════════════════════════════════════════════════
# windy soul
# ═══════════════════════════════════════════════════════════════════════


def cmd_soul(args: argparse.Namespace) -> None:
    """Personality management — show, edit, preset, sliders."""
    action = getattr(args, "action", None)

    if action is None:
        _soul_show()
    elif action == "edit":
        _soul_edit()
    elif action == "preset":
        preset_name = getattr(args, "preset_name", "")
        _soul_preset(preset_name)
    elif action == "sliders":
        _soul_sliders()


def _soul_show() -> None:
    """Show SOUL.md summary."""
    soul_file = PROJECT_ROOT / "SOUL.md"
    if not soul_file.exists():
        console.print("[dim]No SOUL.md found. Run [bold]windy soul edit[/bold] to create one.[/dim]")
        return

    content = soul_file.read_text()
    lines = content.strip().splitlines()
    preview = lines[:10]

    console.print("[bold cyan]SOUL.md[/bold cyan]")
    console.print()
    for line in preview:
        console.print(f"  {line}")

    remaining = len(lines) - 10
    if remaining > 0:
        console.print(f"  [dim]... {remaining} more lines[/dim]")
    console.print()


def _soul_edit() -> None:
    """Open SOUL.md in editor."""
    soul_file = PROJECT_ROOT / "SOUL.md"
    if not soul_file.exists():
        soul_file.write_text("# Soul\n\nDescribe your agent's personality here.\n")

    editor = os.environ.get("EDITOR", "vim")
    console.print(f"  [dim]Opening SOUL.md in {editor}...[/dim]")
    subprocess.run([editor, str(soul_file)])


_SOUL_PRESETS = {
    "buddy": "Friendly, casual, helpful. Uses humor and encouragement.",
    "engineer": "Precise, technical, concise. Focuses on accuracy and efficiency.",
    "powerhouse": "Aggressive productivity. Drives tasks to completion fast.",
    "coder": "Code-focused. Minimal prose, maximum code. Prefers showing over telling.",
    "friend": "Warm, empathetic, conversational. Prioritizes emotional connection.",
    "writer": "Eloquent, creative, expressive. Rich vocabulary and metaphors.",
    "researcher": "Thorough, analytical, citation-heavy. Explores every angle.",
    "silent": "Minimal output. Only speaks when necessary. Terse and direct.",
}


def _soul_preset(preset_name: str) -> None:
    """Show available presets or switch to one."""
    if not preset_name:
        # List all presets
        table = Table(title="Soul Presets", border_style="cyan")
        table.add_column("Preset", style="bold cyan")
        table.add_column("Description")

        for name, desc in _SOUL_PRESETS.items():
            table.add_row(name, desc)

        console.print(table)
        console.print()
        console.print("[dim]Usage: [bold]windy soul preset <name>[/bold][/dim]")
        return

    if preset_name not in _SOUL_PRESETS:
        console.print(f"[red]Unknown preset '{preset_name}'. Available: {', '.join(_SOUL_PRESETS.keys())}[/red]")
        return

    soul_file = PROJECT_ROOT / "SOUL.md"
    desc = _SOUL_PRESETS[preset_name]
    content = f"""# Soul — {preset_name.title()} Preset

## Personality
{desc}

## Preset
{preset_name}

---
*Generated by `windy soul preset {preset_name}`. Edit freely.*
"""
    soul_file.write_text(content)
    console.print(f"  [green]✓[/green] Switched to [bold]{preset_name}[/bold] preset")
    console.print(f"  [dim]{desc}[/dim]")


def _soul_sliders() -> None:
    """Show personality sliders from DB soul table."""
    conn = _open_db()
    if not conn:
        console.print("[dim]Database not found. Start Windy Fly first.[/dim]")
        return

    try:
        cursor = conn.execute(
            "SELECT key, value FROM soul WHERE key LIKE 'slider_%' ORDER BY key"
        )
        rows = cursor.fetchall()
    except Exception:
        console.print("[dim]No soul table found in database.[/dim]")
        conn.close()
        return

    conn.close()

    if not rows:
        console.print("[dim]No personality sliders configured.[/dim]")
        return

    table = Table(title="Personality Sliders", border_style="cyan")
    table.add_column("Slider", style="bold cyan")
    table.add_column("Value", style="bold")

    for key, value in rows:
        slider_name = key.replace("slider_", "").replace("_", " ").title()
        table.add_row(slider_name, str(value))

    console.print(table)


# ═══════════════════════════════════════════════════════════════════════
# windy budget
# ═══════════════════════════════════════════════════════════════════════


def cmd_budget(args: argparse.Namespace) -> None:
    """Cost tracking — today, month, history, set."""
    action = getattr(args, "action", None)

    if action is None:
        _budget_today()
    elif action == "month":
        _budget_month()
    elif action == "history":
        _budget_history()
    elif action == "set":
        amount = getattr(args, "amount", "")
        _budget_set(amount)


def _get_daily_budget() -> float:
    """Read daily_budget from windyfly.toml."""
    toml_file = PROJECT_ROOT / "windyfly.toml"
    if toml_file.exists():
        try:
            import tomllib
            with open(toml_file, "rb") as f:
                config = tomllib.load(f)
            return float(config.get("budget", {}).get("daily_budget", 0))
        except Exception as e:
            logger.debug("Failed to read daily budget config: %s", e)
    return 0.0


def _budget_today() -> None:
    """Show today's spend vs daily budget."""
    conn = _open_db()
    if not conn:
        console.print("[dim]Database not found. No cost data available.[/dim]")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    try:
        cursor = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger WHERE date(created_at) = ?",
            (today,),
        )
        spent = cursor.fetchone()[0]
    except Exception:
        spent = 0.0
        console.print("[dim]No cost_ledger table found.[/dim]")
        conn.close()
        return

    conn.close()

    budget = _get_daily_budget()
    console.print(f"  [bold]Today's spend:[/bold]   ${spent:.4f}")
    if budget > 0:
        pct = (spent / budget) * 100
        color = "green" if pct < 80 else "yellow" if pct < 100 else "red"
        console.print(f"  [bold]Daily budget:[/bold]    ${budget:.2f}")
        console.print(f"  [bold]Used:[/bold]            [{color}]{pct:.1f}%[/{color}]")
    else:
        console.print("  [dim]No daily budget set. Use [bold]windy budget set <amount>[/bold].[/dim]")


def _budget_month() -> None:
    """Show this month's spend."""
    conn = _open_db()
    if not conn:
        console.print("[dim]Database not found. No cost data available.[/dim]")
        return

    month_prefix = datetime.now().strftime("%Y-%m")
    try:
        cursor = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger WHERE created_at LIKE ?",
            (f"{month_prefix}%",),
        )
        spent = cursor.fetchone()[0]
        cursor2 = conn.execute(
            "SELECT COUNT(*) FROM cost_ledger WHERE created_at LIKE ?",
            (f"{month_prefix}%",),
        )
        count = cursor2.fetchone()[0]
    except Exception:
        console.print("[dim]No cost_ledger table found.[/dim]")
        conn.close()
        return

    conn.close()

    console.print(f"  [bold]Month:[/bold]         {month_prefix}")
    console.print(f"  [bold]Total spend:[/bold]   ${spent:.4f}")
    console.print(f"  [bold]Transactions:[/bold]  {count}")


def _budget_history() -> None:
    """Show last 7 days in a table."""
    conn = _open_db()
    if not conn:
        console.print("[dim]Database not found. No cost data available.[/dim]")
        return

    table = Table(title="Cost History (Last 7 Days)", border_style="cyan")
    table.add_column("Date", style="bold")
    table.add_column("Spend", style="bold cyan", justify="right")
    table.add_column("Transactions", justify="right")

    try:
        cursor = conn.execute(
            """SELECT date(created_at) as day, SUM(cost_usd), COUNT(*)
               FROM cost_ledger
               WHERE date(created_at) >= date('now', '-7 days')
               GROUP BY day ORDER BY day DESC"""
        )
        rows = cursor.fetchall()
    except Exception:
        console.print("[dim]No cost_ledger table found.[/dim]")
        conn.close()
        return

    conn.close()

    if not rows:
        console.print("[dim]No cost data in the last 7 days.[/dim]")
        return

    for day, cost, count in rows:
        table.add_row(day, f"${cost:.4f}", str(count))

    console.print(table)


def _budget_set(amount: str) -> None:
    """Update daily_budget in windyfly.toml."""
    if not amount:
        console.print("[red]Usage: windy budget set <amount>[/red]")
        return

    try:
        budget_val = float(amount)
    except ValueError:
        console.print(f"[red]Invalid amount: '{amount}'. Must be a number.[/red]")
        return

    toml_file = PROJECT_ROOT / "windyfly.toml"
    if not toml_file.exists():
        console.print("[yellow]No windyfly.toml found. Run [bold]windy init[/bold] first.[/yellow]")
        return

    content = toml_file.read_text()
    lines = content.splitlines()
    in_budget = False
    found = False
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped == "[budget]":
            in_budget = True
        elif stripped.startswith("[") and stripped.endswith("]"):
            # If we were in [budget] and didn't find the key, add it before leaving
            if in_budget and not found:
                new_lines.append(f"daily_budget = {budget_val}")
                found = True
            in_budget = False

        if in_budget and (stripped.startswith("daily_budget =") or stripped.startswith("daily_budget=")):
            new_lines.append(f"daily_budget = {budget_val}")
            found = True
            continue

        new_lines.append(line)

    # If [budget] section doesn't exist, add it
    if not found:
        if in_budget:
            # We were in budget section at end of file but didn't find key
            new_lines.append(f"daily_budget = {budget_val}")
        else:
            new_lines.append("")
            new_lines.append("[budget]")
            new_lines.append(f"daily_budget = {budget_val}")

    toml_file.write_text("\n".join(new_lines) + "\n")
    console.print(f"  [green]✓[/green] Daily budget set to [bold]${budget_val:.2f}[/bold]")


# ═══════════════════════════════════════════════════════════════════════
# windy memory
# ═══════════════════════════════════════════════════════════════════════


def cmd_memory(args: argparse.Namespace) -> None:
    """Memory operations — stats, search, nodes, intents, export, clear."""
    action = getattr(args, "action", "stats")

    if action == "stats":
        _memory_stats()
    elif action == "search":
        query = getattr(args, "query", "")
        _memory_search(query)
    elif action == "nodes":
        _memory_nodes()
    elif action == "intents":
        _memory_intents()
    elif action == "export":
        _memory_export()
    elif action == "clear":
        confirm = getattr(args, "confirm", False)
        _memory_clear(confirm)


def _memory_stats() -> None:
    """Show memory database statistics."""
    db_path = _get_db_path()
    if not db_path.exists():
        console.print("[dim]Database not found. No memory data available.[/dim]")
        return

    import sqlite3
    conn = sqlite3.connect(str(db_path))

    console.print("[bold cyan]Memory Statistics[/bold cyan]")
    console.print()

    # DB file size
    size_mb = db_path.stat().st_size / (1024 * 1024)
    console.print(f"  [bold]DB size:[/bold]       {size_mb:.2f} MB")

    # Table counts
    stat_tables = {
        "nodes": "Nodes",
        "episodes": "Episodes",
        "intents": "Intents",
        "skills": "Skills",
    }

    for table_name, label in stat_tables.items():
        try:
            cursor = conn.execute(f"SELECT COUNT(*) FROM [{table_name}]")
            count = cursor.fetchone()[0]
            console.print(f"  [bold]{label}:[/bold]  {' ' * (12 - len(label))}{count}")
        except Exception:
            console.print(f"  [bold]{label}:[/bold]  {' ' * (12 - len(label))}[dim]table not found[/dim]")

    conn.close()


def _memory_search(query: str) -> None:
    """Search episodes_fts for a query."""
    if not query:
        console.print("[red]Usage: windy memory search <query>[/red]")
        return

    conn = _open_db()
    if not conn:
        console.print("[dim]Database not found.[/dim]")
        return

    try:
        cursor = conn.execute(
            """SELECT rowid, snippet(episodes_fts, 0, '>>>', '<<<', '...', 30)
               FROM episodes_fts WHERE episodes_fts MATCH ?
               LIMIT 10""",
            (query,),
        )
        rows = cursor.fetchall()
    except Exception as e:
        console.print(f"[dim]Search failed: {e}[/dim]")
        conn.close()
        return

    conn.close()

    if not rows:
        console.print(f"[dim]No results for '{query}'.[/dim]")
        return

    console.print(f"[bold cyan]Search results for '{query}'[/bold cyan]")
    console.print()
    for i, (rowid, snippet) in enumerate(rows, 1):
        console.print(f"  [bold]{i}.[/bold] [dim](row {rowid})[/dim] {snippet}")
    console.print()


def _memory_nodes() -> None:
    """List all nodes."""
    conn = _open_db()
    if not conn:
        console.print("[dim]Database not found.[/dim]")
        return

    try:
        cursor = conn.execute(
            "SELECT type, name, confidence FROM nodes ORDER BY type, name"
        )
        rows = cursor.fetchall()
    except Exception:
        console.print("[dim]No nodes table found.[/dim]")
        conn.close()
        return

    conn.close()

    if not rows:
        console.print("[dim]No nodes in memory.[/dim]")
        return

    table = Table(title="Memory Nodes", border_style="cyan")
    table.add_column("Type", style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Confidence", justify="right")

    for node_type, name, confidence in rows:
        table.add_row(str(node_type), str(name), f"{confidence:.2f}" if confidence else "—")

    console.print(table)


def _memory_intents() -> None:
    """List active intents."""
    conn = _open_db()
    if not conn:
        console.print("[dim]Database not found.[/dim]")
        return

    try:
        cursor = conn.execute(
            "SELECT id, description, status, created_at FROM intents WHERE status = 'active' ORDER BY created_at DESC"
        )
        rows = cursor.fetchall()
    except Exception:
        console.print("[dim]No intents table found.[/dim]")
        conn.close()
        return

    conn.close()

    if not rows:
        console.print("[dim]No active intents.[/dim]")
        return

    table = Table(title="Active Intents", border_style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Description", style="bold")
    table.add_column("Status", style="bold cyan")
    table.add_column("Created", style="dim")

    for intent_id, desc, status, created in rows:
        table.add_row(str(intent_id), str(desc), str(status), str(created))

    console.print(table)


def _memory_export() -> None:
    """Dump all tables to a JSON file."""
    import json

    conn = _open_db()
    if not conn:
        console.print("[dim]Database not found.[/dim]")
        return

    export_data: dict = {}

    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]

        for table_name in tables:
            try:
                cur = conn.execute(f"SELECT * FROM [{table_name}]")
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                export_data[table_name] = [
                    dict(zip(columns, row)) for row in rows
                ]
            except Exception as e:
                logger.debug("Failed to export table %s: %s", table_name, e)
                export_data[table_name] = []

    except Exception as e:
        console.print(f"[red]Export failed: {e}[/red]")
        conn.close()
        return

    conn.close()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_file = PROJECT_ROOT / "data" / f"memory_export_{timestamp}.json"
    export_file.parent.mkdir(parents=True, exist_ok=True)

    with open(export_file, "w") as f:
        json.dump(export_data, f, indent=2, default=str)

    console.print(f"  [green]✓[/green] Exported to [bold]{export_file}[/bold]")


def _memory_clear(confirm: bool) -> None:
    """Clear all memory data. Requires --confirm flag."""
    if not confirm:
        console.print("[yellow]This will DELETE all memory data (episodes, nodes, intents).[/yellow]")
        console.print("[yellow]Run with [bold]--confirm[/bold] to proceed.[/yellow]")
        return

    conn = _open_db()
    if not conn:
        console.print("[dim]Database not found. Nothing to clear.[/dim]")
        return

    tables_cleared = []
    for table_name in ["episodes", "nodes", "intents"]:
        try:
            conn.execute(f"DELETE FROM [{table_name}]")
            tables_cleared.append(table_name)
        except Exception as e:
            logger.debug("Failed to clear table %s: %s", table_name, e)

    conn.commit()
    conn.close()

    if tables_cleared:
        console.print(f"  [green]✓[/green] Cleared: {', '.join(tables_cleared)}")
    else:
        console.print("[dim]No tables to clear.[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# windy skills
# ═══════════════════════════════════════════════════════════════════════


def cmd_skills(args: argparse.Namespace) -> None:
    """Skill management — list promoted, all, run, eval."""
    action = getattr(args, "action", None)

    if action is None:
        _skills_list(promoted_only=True)
    elif action == "all":
        _skills_list(promoted_only=False)
    elif action == "run":
        skill_name = getattr(args, "skill_name", "")
        _skills_run(skill_name)
    elif action == "eval":
        skill_name = getattr(args, "skill_name", "")
        _skills_eval(skill_name)


def _skills_list(promoted_only: bool = True) -> None:
    """List skills from the database."""
    conn = _open_db()
    if not conn:
        console.print("[dim]Database not found. No skills available.[/dim]")
        return

    try:
        if promoted_only:
            cursor = conn.execute(
                "SELECT name, description, promoted, created_at FROM skills WHERE promoted = 1 ORDER BY name"
            )
        else:
            cursor = conn.execute(
                "SELECT name, description, promoted, created_at FROM skills ORDER BY name"
            )
        rows = cursor.fetchall()
    except Exception:
        console.print("[dim]No skills table found.[/dim]")
        conn.close()
        return

    conn.close()

    if not rows:
        label = "promoted skills" if promoted_only else "skills"
        console.print(f"[dim]No {label} found.[/dim]")
        return

    title = "Promoted Skills" if promoted_only else "All Skills"
    table = Table(title=title, border_style="cyan")
    table.add_column("Name", style="bold cyan")
    table.add_column("Description")
    table.add_column("Promoted", justify="center")
    table.add_column("Created", style="dim")

    for name, desc, promoted, created in rows:
        prom_icon = "[green]Yes[/green]" if promoted else "[dim]No[/dim]"
        table.add_row(str(name), str(desc or ""), prom_icon, str(created or ""))

    console.print(table)


def _skills_run(skill_name: str) -> None:
    """Run a skill by name."""
    if not skill_name:
        console.print("[red]Usage: windy skills run <skill-name>[/red]")
        return

    console.print("  [yellow]Skill execution not yet implemented.[/yellow]")
    console.print(f"  [dim]Skill: {skill_name}[/dim]")


def _skills_eval(skill_name: str) -> None:
    """Evaluate a skill through the 3-gate process."""
    if not skill_name:
        console.print("[red]Usage: windy skills eval <skill-name>[/red]")
        return

    console.print("  [yellow]Skill evaluation (3-gate) not yet implemented.[/yellow]")
    console.print(f"  [dim]Skill: {skill_name}[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# windy reset
# ═══════════════════════════════════════════════════════════════════════


def cmd_reset(args: argparse.Namespace) -> None:
    """Factory reset — soft or hard."""
    soft = getattr(args, "soft", False)
    hard = getattr(args, "hard", False)

    if not soft and not hard:
        console.print("[yellow]Specify [bold]--soft[/bold] or [bold]--hard[/bold].[/yellow]")
        console.print()
        console.print("  [bold]--soft[/bold]  Clear DB tables but keep config files")
        console.print("  [bold]--hard[/bold]  Delete data/, .env, windyfly.toml, SOUL.md")
        return

    mode = "HARD" if hard else "SOFT"
    console.print(f"[bold red]WARNING: {mode} RESET[/bold red]")
    console.print()

    if hard:
        console.print("This will DELETE:")
        console.print("  - data/ directory (database, logs, certificates)")
        console.print("  - .env file (API keys, configuration)")
        console.print("  - windyfly.toml (settings)")
        console.print("  - SOUL.md (personality)")
    else:
        console.print("This will CLEAR all database tables but keep config files.")

    console.print()

    try:
        confirmation = input("Type RESET to confirm: ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Cancelled.[/dim]")
        return

    if confirmation != "RESET":
        console.print("[dim]Cancelled — confirmation did not match.[/dim]")
        return

    if hard:
        # Delete data directory
        data_dir = PROJECT_ROOT / "data"
        if data_dir.exists():
            shutil.rmtree(str(data_dir))
            console.print("  [green]✓[/green] Deleted data/")

        # Delete config files
        for fname in [".env", "windyfly.toml", "SOUL.md"]:
            fpath = PROJECT_ROOT / fname
            if fpath.exists():
                fpath.unlink()
                console.print(f"  [green]✓[/green] Deleted {fname}")

        console.print()
        console.print("[bold green]Hard reset complete.[/bold green] Run [bold]windy init[/bold] to set up again.")

    else:
        # Soft reset — clear DB tables
        conn = _open_db()
        if not conn:
            console.print("[dim]Database not found. Nothing to clear.[/dim]")
            return

        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = [row[0] for row in cursor.fetchall()]

            cleared = 0
            for table_name in tables:
                if table_name.startswith("sqlite_"):
                    continue
                try:
                    conn.execute(f"DELETE FROM [{table_name}]")
                    cleared += 1
                except Exception as e:
                    logger.debug("Failed to clear table %s during reset: %s", table_name, e)

            conn.commit()
            console.print(f"  [green]✓[/green] Cleared {cleared} tables")

        except Exception as e:
            console.print(f"  [red]✗ Reset failed: {e}[/red]")
        finally:
            conn.close()

        console.print()
        console.print("[bold green]Soft reset complete.[/bold green] Config files preserved.")


# ═══════════════════════════════════════════════════════════════════════
# windy export
# ═══════════════════════════════════════════════════════════════════════


def cmd_export(_args: argparse.Namespace) -> None:
    """Backup everything to a tar.gz archive."""
    import tarfile

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"windyfly-backup-{timestamp}.tar.gz"
    backup_path = PROJECT_ROOT / backup_name

    files_to_backup: list[tuple[Path, str]] = []

    # Database
    db_path = _get_db_path()
    if db_path.exists():
        files_to_backup.append((db_path, "data/windyfly.db"))

    # Config files
    for fname in [".env", "windyfly.toml", "SOUL.md"]:
        fpath = PROJECT_ROOT / fname
        if fpath.exists():
            files_to_backup.append((fpath, fname))

    # Sounds directory
    sounds_dir = PROJECT_ROOT / "data" / "sounds"
    if sounds_dir.exists():
        for sound_file in sounds_dir.iterdir():
            if sound_file.is_file():
                files_to_backup.append((sound_file, f"data/sounds/{sound_file.name}"))

    # Birth certificates
    import glob
    for cert in glob.glob(str(PROJECT_ROOT / "data" / "birth_certificate_*.pdf")):
        cert_path = Path(cert)
        files_to_backup.append((cert_path, f"data/{cert_path.name}"))

    if not files_to_backup:
        console.print("[dim]Nothing to backup.[/dim]")
        return

    try:
        with tarfile.open(str(backup_path), "w:gz") as tar:
            for file_path, arcname in files_to_backup:
                tar.add(str(file_path), arcname=arcname)
                console.print(f"  [dim]+ {arcname}[/dim]")

        size_mb = backup_path.stat().st_size / (1024 * 1024)
        console.print()
        console.print(f"  [green]✓[/green] Backup created: [bold]{backup_path}[/bold] ({size_mb:.2f} MB)")
        console.print(f"  [dim]{len(files_to_backup)} files archived[/dim]")

    except Exception as e:
        console.print(f"  [red]✗ Backup failed: {e}[/red]")


# ═══════════════════════════════════════════════════════════════════════
# windy import
# ═══════════════════════════════════════════════════════════════════════


def cmd_import(args: argparse.Namespace) -> None:
    """Restore from a backup tar.gz file."""
    import tarfile

    backup_file = getattr(args, "file", "")
    if not backup_file:
        console.print("[red]Usage: windy import <path-to-backup.tar.gz>[/red]")
        return

    backup_path = Path(backup_file)
    if not backup_path.exists():
        console.print(f"[red]Backup file not found: {backup_path}[/red]")
        return

    if not tarfile.is_tarfile(str(backup_path)):
        console.print(f"[red]Not a valid tar.gz file: {backup_path}[/red]")
        return

    console.print(f"  [cyan]Restoring from {backup_path.name}...[/cyan]")
    console.print()

    try:
        with tarfile.open(str(backup_path), "r:gz") as tar:
            # Safety check — no path traversal
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    console.print(f"  [red]Unsafe path in archive: {member.name}. Aborting.[/red]")
                    return

            # Ensure data directory exists
            (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)

            # Extract all files
            for member in tar.getmembers():
                tar.extract(member, path=str(PROJECT_ROOT))
                console.print(f"  [dim]Restored: {member.name}[/dim]")

        console.print()
        console.print(f"  [green]✓[/green] Restore complete from [bold]{backup_path.name}[/bold]")

    except Exception as e:
        console.print(f"  [red]✗ Restore failed: {e}[/red]")


# ═══════════════════════════════════════════════════════════════════════
# windy repl
# ═══════════════════════════════════════════════════════════════════════


def cmd_repl(_args: argparse.Namespace) -> None:
    """Start a developer REPL with agent database and config pre-loaded."""
    import code

    banner = "🪰 Windy Fly Developer REPL\nAvailable: db, config, console"
    ns: dict = {"console": console}

    try:
        from windyfly.config import load_config
        ns["config"] = load_config()
    except Exception as e:
        logger.debug("Failed to load config for REPL: %s", e)

    try:
        from windyfly.memory.database import Database
        db_path_str = "data/windyfly.db"
        if isinstance(ns.get("config"), dict):
            db_path_str = ns["config"].get("memory", {}).get("db_path", db_path_str)
        db_path = PROJECT_ROOT / db_path_str
        if db_path.exists():
            ns["db"] = Database(str(db_path))
    except Exception as e:
        logger.debug("Failed to load database for REPL: %s", e)

    code.interact(banner=banner, local=ns)
