"""Windy Fly CLI — unified entry point.

Commands::

    windy go               — One-command quickstart (paste a key, done)
    windy init             — Interactive setup wizard (terminal TUI)
    windy setup            — Browser-based setup wizard (opens localhost)
    windy start            — Start brain + gateway (opens dashboard)
    windy start --cli      — Start brain in CLI chat mode (no gateway)
    windy chat             — Start CLI chat mode (alias for start --cli)
    windy stop             — Stop all Windy Fly processes
    windy restart          — Stop + start in one shot
    windy status           — Show what's running
    windy doctor           — Diagnose your installation
    windy test             — Run self-test (verify agent works)
    windy update           — Pull latest code + sync dependencies
    windy logs [component] — Tail brain/gateway logs
    windy config show      — View current configuration
    windy config set K V   — Set a config value
    windy config reset     — Re-run setup wizard
    windy config path      — Show config file locations
    windy version          — Show version and environment info
"""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

from rich.console import Console

from windyfly.platform import (
    get_data_dir,
    get_log_path,
    get_pid_path,
    kill_by_name,
    process_alive,
    process_terminate,
)

console = Console()
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PID_FILE = get_pid_path(PROJECT_ROOT)


def cmd_init(_args: argparse.Namespace) -> None:
    """Run the interactive setup wizard."""
    from windyfly.setup_wizard import run_wizard
    run_wizard()


def cmd_start(args: argparse.Namespace) -> None:
    """Start the Windy Fly stack."""
    import webbrowser

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    env_file = PROJECT_ROOT / ".env"
    config_file = PROJECT_ROOT / "windyfly.toml"

    if not env_file.exists() or not config_file.exists():
        console.print("[yellow]⚠ Not configured yet. Running setup wizard first...[/yellow]")
        console.print()
        cmd_init(args)
        return

    # Check if already running
    if PID_FILE.exists():
        pids = PID_FILE.read_text().strip().split("\n")
        alive = [pid for pid in pids if process_alive(int(pid))]
        if alive:
            console.print(f"[yellow]⚠ Windy Fly is already running (PIDs: {', '.join(alive)})[/yellow]")
            console.print("  Run [bold]windy stop[/bold] first, or [bold]windy status[/bold] to check.")
            return

    console.print("[bold cyan]🪰 Starting Windy Fly...[/bold cyan]")
    console.print()

    pids: list[int] = []

    if args.cli:
        # CLI-only mode: run brain interactively in foreground
        console.print("  [cyan]Starting brain in CLI mode...[/cyan]")
        console.print("  [dim]Type your messages below. Ctrl+C to exit.[/dim]")
        console.print()
        try:
            subprocess.run(
                ["uv", "run", "python", "-m", "windyfly.main", "--channel", "cli"],
                cwd=str(PROJECT_ROOT),
            )
        except KeyboardInterrupt:
            console.print("\n  [dim]Goodbye! 🪰[/dim]")
        return

    # Ensure log directory exists
    get_data_dir(PROJECT_ROOT)

    # Full stack: brain + gateway
    # NOTE: Log file handles are intentionally kept open for the lifetime
    # of the subprocess. They'll be closed when the process exits or when
    # the user runs `windy stop`.
    brain_log = open(get_log_path(PROJECT_ROOT, "brain"), "a")  # noqa: SIM115
    brain_proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "windyfly.bridge.uds_server"],
        cwd=str(PROJECT_ROOT),
        stdout=brain_log,
        stderr=subprocess.STDOUT,
    )
    pids.append(brain_proc.pid)
    console.print(f"  [green]✓[/green] Brain started [dim](PID {brain_proc.pid})[/dim]")

    # Start gateway
    gateway_dir = PROJECT_ROOT / "gateway"
    gateway_log = open(get_log_path(PROJECT_ROOT, "gateway"), "a")  # noqa: SIM115
    gateway_proc = subprocess.Popen(
        ["bun", "run", "src/server.ts"],
        cwd=str(gateway_dir),
        stdout=gateway_log,
        stderr=subprocess.STDOUT,
    )
    pids.append(gateway_proc.pid)
    console.print(f"  [green]✓[/green] Gateway started [dim](PID {gateway_proc.pid})[/dim]")

    # Write PID file
    PID_FILE.write_text("\n".join(str(p) for p in pids) + "\n")

    # Wait for gateway to be ready
    time.sleep(2)

    # ── The Hatching Ceremony ──
    from windyfly.hatching import play_hatching, show_ecosystem_status
    play_hatching(animate=True)
    show_ecosystem_status()

    console.print("  [cyan]Brain log:[/cyan]    data/brain.log")
    console.print("  [cyan]Gateway log:[/cyan]  data/gateway.log")
    console.print()
    console.print("  Run [bold]windy stop[/bold] to shut down.")
    console.print()

    # Open browser (unless --no-browser)
    if not getattr(args, "no_browser", False):
        try:
            webbrowser.open("http://localhost:3000")
        except Exception:
            pass


def cmd_stop(_args: argparse.Namespace) -> None:
    """Stop all Windy Fly processes."""
    if not PID_FILE.exists():
        console.print("[dim]No PID file found. Nothing to stop.[/dim]")
        _do_kill_by_name()
        return

    pids = PID_FILE.read_text().strip().split("\n")
    stopped = 0

    for pid_str in pids:
        try:
            pid = int(pid_str.strip())
            if process_alive(pid):
                if process_terminate(pid):
                    console.print(f"  [green]✓[/green] Stopped PID {pid}")
                    stopped += 1
                else:
                    console.print(f"  [yellow]⚠ Could not stop PID {pid}[/yellow]")
            else:
                console.print(f"  [dim]PID {pid} already stopped[/dim]")
        except (ValueError, OSError) as e:
            console.print(f"  [yellow]⚠ Could not stop PID {pid_str}: {e}[/yellow]")

    PID_FILE.unlink(missing_ok=True)

    if stopped:
        console.print(f"\n  [green]✓ Stopped {stopped} process(es)[/green]")
    else:
        console.print("\n  [dim]No running processes found[/dim]")
        _do_kill_by_name()


def cmd_status(_args: argparse.Namespace) -> None:
    """Show comprehensive agent status using the rich tree display."""
    from windyfly.cli_status import print_status
    print_status()


def cmd_setup(_args: argparse.Namespace) -> None:
    """Launch the browser-based setup wizard."""
    import webbrowser

    # Check if gateway is already running
    try:
        import httpx
        r = httpx.get("http://localhost:3000/api/health", timeout=2)
        if r.status_code == 200:
            console.print("[bold cyan]🪰 Opening browser setup wizard...[/bold cyan]")
            console.print()
            console.print("  [cyan]Setup wizard:[/cyan]  http://localhost:3000/setup.html")
            console.print()
            webbrowser.open("http://localhost:3000/setup.html")
            return
    except Exception:
        pass

    # Gateway not running — start it first
    console.print("[bold cyan]🪰 Starting gateway for browser setup...[/bold cyan]")
    console.print()

    gateway_dir = PROJECT_ROOT / "gateway"
    get_data_dir(PROJECT_ROOT)
    gateway_log = open(get_log_path(PROJECT_ROOT, "gateway"), "a")
    gateway_proc = subprocess.Popen(
        ["bun", "run", "src/server.ts"],
        cwd=str(gateway_dir),
        stdout=gateway_log,
        stderr=subprocess.STDOUT,
    )

    # Wait for gateway to be ready
    time.sleep(2)

    console.print(f"  [green]✓[/green] Gateway started [dim](PID {gateway_proc.pid})[/dim]")
    console.print()
    console.print("  [cyan]Setup wizard:[/cyan]  http://localhost:3000/setup.html")
    console.print()
    console.print("  [dim]Complete the wizard in your browser. Press Ctrl+C when done.[/dim]")
    console.print()

    webbrowser.open("http://localhost:3000/setup.html")

    # Keep running until user presses Ctrl+C
    try:
        gateway_proc.wait()
    except KeyboardInterrupt:
        gateway_proc.terminate()
        console.print("\n  [dim]Gateway stopped. Run `windy start` to launch the full stack.[/dim]")


def _do_kill_by_name() -> None:
    """Fallback: try to kill processes by name."""
    kill_by_name([
        "windyfly.bridge.uds_server",
        "windyfly.main",
        "bun run src/server.ts",
    ])
    console.print("  [dim]Sent terminate signal to any matching processes[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════


def main() -> None:
    """CLI entry point — registered as ``windy`` command via pyproject.toml."""
    parser = argparse.ArgumentParser(
        prog="windy",
        description="🪰 Windy Fly — Your AI. Your Rules. Your Ecosystem.",
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # windy init
    sub.add_parser("init", help="Run the interactive setup wizard")

    # windy start
    start_parser = sub.add_parser("start", help="Start the Windy Fly stack")
    start_parser.add_argument(
        "--cli", action="store_true",
        help="Run in CLI chat mode (no gateway/dashboard)",
    )

    # windy setup
    sub.add_parser("setup", help="Open browser-based setup wizard")

    # windy stop
    sub.add_parser("stop", help="Stop all Windy Fly processes")

    # windy restart
    restart_parser = sub.add_parser("restart", help="Stop + start in one shot")
    restart_parser.add_argument(
        "--cli", action="store_true",
        help="Restart in CLI chat mode (no gateway/dashboard)",
    )

    # windy status
    sub.add_parser("status", help="Show status of running processes")

    # windy doctor
    sub.add_parser("doctor", help="Diagnose your Windy Fly installation")

    # windy update
    sub.add_parser("update", help="Pull latest code and sync dependencies")

    # windy logs
    logs_parser = sub.add_parser("logs", help="Tail brain/gateway logs")
    logs_parser.add_argument(
        "component", nargs="?", default="all",
        choices=["all", "brain", "gateway"],
        help="Which component's logs to show (default: all)",
    )
    logs_parser.add_argument(
        "-f", "--follow", action="store_true",
        help="Follow log output in real time",
    )
    logs_parser.add_argument(
        "-n", "--lines", type=int, default=50,
        help="Number of lines to show (default: 50)",
    )

    # windy config
    config_parser = sub.add_parser("config", help="View or edit configuration")
    config_sub = config_parser.add_subparsers(dest="action", help="Config action")
    config_sub.add_parser("show", help="Display current configuration")
    config_set = config_sub.add_parser("set", help="Set a config value")
    config_set.add_argument("key", help="Key in section.name format (e.g., agent.default_model)")
    config_set.add_argument("value", help="New value")
    config_sub.add_parser("reset", help="Re-run the setup wizard")
    config_sub.add_parser("path", help="Show config file locations")

    # windy version
    sub.add_parser("version", help="Show version and environment info")

    # windy chat — alias for start --cli
    sub.add_parser("chat", help="Start CLI chat mode (alias for start --cli)")

    # windy test — self-test
    sub.add_parser("test", help="Run self-test to verify the agent works")

    # windy ecosystem — show ecosystem connections
    sub.add_parser("ecosystem", help="Show ecosystem connection status")

    # windy go — the one-command quickstart
    go_parser = sub.add_parser("go", help="One-command quickstart — paste a key and go")
    go_parser.add_argument(
        "--key", "-k",
        help="API key (auto-detects provider). Skips all prompts.",
    )
    go_parser.add_argument(
        "--model", "-m",
        help="Override default model (e.g., gpt-4o, claude-3-5-sonnet-latest)",
    )
    go_parser.add_argument(
        "--preset", "-p",
        choices=["buddy", "engineer", "powerhouse", "coder", "friend", "writer", "researcher", "silent"],
        help="Personality preset (default: buddy)",
    )
    go_parser.add_argument(
        "--no-browser", action="store_true",
        help="Don't open browser (for headless servers)",
    )

    args = parser.parse_args()

    if args.command is None:
        # No subcommand — run quickstart if not configured, help if configured
        env_file = PROJECT_ROOT / ".env"
        config_file = PROJECT_ROOT / "windyfly.toml"
        if not env_file.exists() or not config_file.exists():
            from windyfly.quickstart import cmd_go
            cmd_go(args)
        else:
            parser.print_help()
        return

    from windyfly.commands import (
        cmd_config,
        cmd_doctor,
        cmd_logs,
        cmd_restart,
        cmd_update,
        cmd_version,
    )
    from windyfly.quickstart import cmd_go

    commands = {
        "go": cmd_go,
        "init": cmd_init,
        "setup": cmd_setup,
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "status": cmd_status,
        "doctor": cmd_doctor,
        "update": cmd_update,
        "logs": cmd_logs,
        "config": cmd_config,
        "version": cmd_version,
        "chat": _cmd_chat,
        "test": _cmd_test,
        "ecosystem": _cmd_ecosystem,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


def _cmd_chat(args: argparse.Namespace) -> None:
    """Start CLI chat mode (alias for `windy start --cli`)."""
    args.cli = True
    args.no_browser = True
    cmd_start(args)


def _cmd_test(_args: argparse.Namespace) -> None:
    """Run the agent self-test."""
    from windyfly.cli_selftest import run_self_test
    run_self_test()


def _cmd_ecosystem(_args: argparse.Namespace) -> None:
    """Show the agent's current ecosystem connections."""
    from windyfly.hatching import show_ecosystem_status
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    show_ecosystem_status()

    # Also show connectivity checks
    _check_ecosystem_connectivity()


def _check_ecosystem_connectivity() -> None:
    """Check live connectivity to ecosystem services."""
    import os

    from rich.table import Table

    table = Table(
        title="Ecosystem Connectivity",
        title_style="bold",
        border_style="dim",
        show_lines=True,
    )
    table.add_column("Service", style="bold", min_width=14)
    table.add_column("Endpoint", min_width=30)
    table.add_column("Status", min_width=8)
    table.add_column("Latency", min_width=8)

    checks = []

    # Eternitas
    passport = os.environ.get("ETERNITAS_PASSPORT", "")
    eternitas_url = os.environ.get("ETERNITAS_API_URL", "")
    if eternitas_url and passport:
        checks.append(("Eternitas", f"{eternitas_url}/api/v1/registry/verify/{passport}"))
    elif eternitas_url:
        checks.append(("Eternitas", f"{eternitas_url}/health"))

    # Windy Pro
    windy_api = os.environ.get("WINDY_API_URL", "")
    if windy_api:
        checks.append(("Windy Pro", f"{windy_api}/health"))

    # Matrix
    matrix_hs = os.environ.get("MATRIX_HOMESERVER", "")
    if matrix_hs:
        checks.append(("Matrix", f"{matrix_hs}/_matrix/client/versions"))

    # Windy Mail
    mail_url = os.environ.get("WINDYMAIL_API_URL", "")
    if mail_url:
        checks.append(("Windy Mail", f"{mail_url}/health"))

    # Windy Cloud
    cloud_url = os.environ.get("WINDY_CLOUD_URL", "")
    if cloud_url:
        checks.append(("Windy Cloud", f"{cloud_url}/api/storage/health"))

    if not checks:
        console.print("  [dim]No ecosystem services configured. Set env vars to enable checks.[/dim]")
        console.print()
        return

    import time
    try:
        import httpx
    except ImportError:
        console.print("  [dim]httpx not available for connectivity checks[/dim]")
        return

    for name, url in checks:
        start = time.time()
        try:
            r = httpx.get(url, timeout=5)
            elapsed = (time.time() - start) * 1000
            if r.status_code < 500:
                table.add_row(name, url, "[green]PASS[/green]", f"{elapsed:.0f}ms")
            else:
                table.add_row(name, url, "[red]FAIL[/red]", f"{r.status_code}")
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            table.add_row(name, url, "[red]FAIL[/red]", f"{elapsed:.0f}ms")

    console.print(table)
    console.print()


if __name__ == "__main__":
    main()
