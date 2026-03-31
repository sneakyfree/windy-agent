"""Windy Fly CLI commands — doctor, update, logs, restart, config, version.

All commands built on top of :mod:`windyfly.platform` for cross-platform
compatibility.  Import individual ``cmd_*`` functions from here and register
them in the argparse router in ``cli.py``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from windyfly.platform import (
    SYSTEM,
    IS_WINDOWS,
    IS_POSIX,
    can_run,
    diagnose,
    get_data_dir,
    get_ipc_config,
    get_log_path,
    get_pid_path,
    process_alive,
)

console = Console()
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Current version — bump on release
VERSION = "0.1.0"


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
    toml_parse_ok = False
    if toml_file.exists():
        try:
            import tomllib
            with open(toml_file, "rb") as f:
                tomllib.load(f)
            toml_parse_ok = True
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
                _doc_row(f"Port {port} ({name})", f"in use", False,
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
        except Exception:
            pass
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
        except Exception:
            pass
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

    console.print()

    # ── Summary ──────────────────────────────────────────────────
    total_checks = len(issues) + len(warnings)
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
        except Exception:
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
            except Exception:
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
        console.print(f"[dim]Following logs... Press Ctrl+C to stop.[/dim]")
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

        if in_section and stripped.startswith(f"{config_key} =") or \
           in_section and stripped.startswith(f"{config_key}="):
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
        console.print(f"  [dim]Run [bold]windy config show[/bold] to see available keys.[/dim]")


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
        except Exception:
            console.print(f"  [bold]uv:[/bold]         installed")
    else:
        console.print(f"  [bold]uv:[/bold]         [red]not found[/red]")

    # Bun version
    if can_run("bun"):
        try:
            bun_ver = subprocess.run(
                ["bun", "--version"], capture_output=True, text=True,
            ).stdout.strip()
            console.print(f"  [bold]Bun:[/bold]        {bun_ver}")
        except Exception:
            console.print(f"  [bold]Bun:[/bold]        installed")
    else:
        console.print(f"  [bold]Bun:[/bold]        [red]not found[/red]")

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
        except Exception:
            pass

    console.print(f"  [bold]Root:[/bold]       {PROJECT_ROOT}")
    console.print()
