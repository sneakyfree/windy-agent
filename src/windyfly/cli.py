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
    windy kill             — Force kill everything (emergency)
    windy ps               — Show running processes
    windy status           — Quick status summary
    windy doctor           — Full health check
    windy test             — Run self-test (verify agent works)
    windy repl             — Developer REPL
    windy logs [component] — Tail brain/gateway logs
    windy debug            — Verbose debug info
    windy ecosystem        — Show ecosystem connections
    windy channels         — Show messaging channels
    windy passport         — Show Eternitas passport
    windy mail             — Show mail status
    windy phone            — Show phone status
    windy cert             — Show birth certificate
    windy config show      — View current configuration
    windy config set K V   — Set a config value
    windy config reset     — Re-run setup wizard
    windy config path      — Show config file locations
    windy model            — Show/change LLM model
    windy soul             — Show/edit personality
    windy budget           — Cost tracking
    windy memory           — Memory operations
    windy skills           — Skill management
    windy update           — Pull latest code + sync dependencies
    windy version          — Show version and environment info
    windy export           — Backup everything
    windy import           — Restore from backup
    windy reset            — Factory reset
    windy help             — Show all commands grouped by category
    windy commands         — List all commands in compact table
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console

from windyfly.platform import (
    get_data_dir,
    get_log_path,
    get_project_root,
    kill_by_name,
    process_alive,
    process_terminate,
    read_pid_file,
    remove_pid_file,
    write_pid_file,
)

logger = logging.getLogger(__name__)
console = Console()
PROJECT_ROOT = get_project_root()


# ─── Wave 12 P1 — SIGTERM cleanup for spawned daemons ────────────────
#
# `windy go` (via _launch → cmd_start with daemon=True) backgrounds
# brain + gateway children in a *new session* so they survive a
# terminal close. That's the intended behaviour for normal exits.
#
# But an **explicit** SIGTERM / SIGINT to the `windy go` parent should
# also take down the children — otherwise `windy go & ; kill $!` leaks
# a bun gateway bound to :3000 (hardening report finding #11).
#
# Strategy:
#   1. When cmd_start spawns children, record their PIDs in module-level
#      state + the PID file.
#   2. Install SIGTERM + SIGINT handlers on the parent. On trip, forward
#      termination to every recorded child, wipe the PID file, then
#      re-raise the signal so the parent exits with the standard code.
#   3. atexit is a *safety net* — it runs iff the signal handler set a
#      "please-cleanup" sentinel but didn't finish the kill. On a
#      normal `cmd_start` return we leave children alone, preserving
#      the daemon-survives-terminal-close promise.

_tracked_child_pids: list[int] = []
_cleanup_already_ran = False
_cleanup_requested_via_signal = False


def _cleanup_tracked_children() -> None:
    """Kill every child this parent spawned. Safe to call repeatedly."""
    global _cleanup_already_ran
    if _cleanup_already_ran:
        return
    _cleanup_already_ran = True
    for pid in list(_tracked_child_pids):
        try:
            if process_alive(pid):
                process_terminate(pid)
        except (OSError, ValueError):
            # Best-effort — a race where the child exited between
            # process_alive and process_terminate is fine.
            pass
    try:
        remove_pid_file(PROJECT_ROOT)
    except OSError:
        pass


def _signal_cleanup_handler(signum: int, _frame) -> None:
    """Trip the cleanup + re-raise the signal under its default disposition."""
    global _cleanup_requested_via_signal
    _cleanup_requested_via_signal = True
    _cleanup_tracked_children()
    # Restore default handler and re-raise so the process exits with the
    # conventional status (128 + signum) rather than SystemExit(0).
    try:
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    except Exception:
        # If signal re-raise fails (e.g. on Windows where SIGTERM's
        # default is terminate-without-handler), fall back to exit.
        os._exit(128 + signum)


_signal_cleanup_installed = False


def _atexit_safety_net() -> None:
    """Finish cleanup if a signal tripped it but we didn't complete."""
    if _cleanup_requested_via_signal and not _cleanup_already_ran:
        _cleanup_tracked_children()


def _install_signal_cleanup() -> None:
    """Register SIGTERM + SIGINT handlers + an atexit safety net.

    Idempotent — repeated calls (e.g. a re-invoked cmd_start after a
    crashed previous attempt) only install the handlers once.
    """
    global _signal_cleanup_installed
    if _signal_cleanup_installed:
        return
    # Python reserves signal handler installation for the main thread;
    # cmd_start always runs on the main thread so this is safe.
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _signal_cleanup_handler)
        except (ValueError, OSError):
            # ValueError: not main thread. OSError: signal unavailable.
            # Either is non-fatal; cleanup just won't fire on that signal.
            pass
    atexit.register(_atexit_safety_net)
    _signal_cleanup_installed = True


# ═══════════════════════════════════════════════════════════════════════
# Core commands (defined here)
# ═══════════════════════════════════════════════════════════════════════


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
    pid_info = read_pid_file(PROJECT_ROOT)
    if pid_info and pid_info.any_alive:
        alive_parts = []
        if pid_info.brain_alive:
            alive_parts.append(f"brain={pid_info.brain}")
        if pid_info.gateway_alive:
            alive_parts.append(f"gateway={pid_info.gateway}")
        console.print(f"[yellow]⚠ Windy Fly is already running ({', '.join(alive_parts)})[/yellow]")
        console.print("  Run [bold]windy stop[/bold] first, or [bold]windy status[/bold] to check.")
        return

    console.print("[bold cyan]🪰 Starting Windy Fly...[/bold cyan]")
    console.print()

    # Non-blocking update check (uses 24h cache, instant if cached)
    try:
        from windyfly.update import check_for_update, apply_update
        from windyfly.config import load_config as _load_cfg
        _cfg = _load_cfg()
        _updates_cfg = _cfg.get("updates", {})
        if _updates_cfg.get("auto_check", True):
            _info = check_for_update()
            if _info:
                if _updates_cfg.get("auto_install", False):
                    console.print("  [cyan]Auto-installing update...[/cyan]")
                    _ok, _msg = apply_update()
                    if _ok:
                        console.print(f"  [green]✓[/green] {_msg}")
                    else:
                        console.print(f"  [yellow]⚠ Auto-update failed:[/yellow] {_msg}")
                else:
                    console.print(
                        f"  [yellow]⬆ Update available:[/yellow] v{_info['current']} → "
                        f"[bold green]v{_info['latest']}[/bold green] — "
                        f"run [bold]windy update[/bold]"
                    )
                    console.print()
    except Exception:
        pass  # Never let update check block startup

    if getattr(args, "cli", False):
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

    daemon = getattr(args, "daemon", False)

    # Ensure log directory exists
    get_data_dir(PROJECT_ROOT)

    # Full stack: brain + gateway
    # In daemon mode, fully detach from the terminal so the process
    # survives after the shell exits. On Unix this uses
    # start_new_session=True; on Windows, CREATE_NEW_PROCESS_GROUP.
    from windyfly.platform import IS_WINDOWS

    popen_extra: dict = {}
    if daemon:
        if IS_WINDOWS:
            popen_extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            popen_extra["start_new_session"] = True

    brain_log = open(get_log_path(PROJECT_ROOT, "brain"), "a")  # noqa: SIM115
    brain_proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "windyfly.bridge.uds_server"],
        cwd=str(PROJECT_ROOT),
        stdout=brain_log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL if daemon else None,
        **popen_extra,
    )
    _tracked_child_pids.append(brain_proc.pid)
    console.print(f"  [green]✓[/green] Brain started [dim](PID {brain_proc.pid})[/dim]")

    # Start gateway (optional — only available in source checkout with Bun)
    gateway_dir = PROJECT_ROOT / "gateway"
    gateway_pid = None
    if gateway_dir.exists() and (gateway_dir / "src" / "server.ts").exists():
        import shutil
        if shutil.which("bun"):
            gateway_log = open(get_log_path(PROJECT_ROOT, "gateway"), "a")  # noqa: SIM115
            gateway_proc = subprocess.Popen(
                ["bun", "run", "src/server.ts"],
                cwd=str(gateway_dir),
                stdout=gateway_log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL if daemon else None,
                **popen_extra,
            )
            gateway_pid = gateway_proc.pid
            _tracked_child_pids.append(gateway_pid)
            console.print(f"  [green]✓[/green] Gateway started [dim](PID {gateway_pid})[/dim]")
        else:
            console.print("  [dim]○ Gateway skipped (Bun not installed)[/dim]")
    else:
        console.print("  [dim]○ Gateway skipped (not available in pip install)[/dim]")

    # Write PID file (new key=value format)
    write_pid_file(PROJECT_ROOT, brain_proc.pid, gateway_pid)

    # Install SIGTERM/SIGINT cleanup now that the children are tracked.
    # An explicit kill of this parent (even via `windy go &; kill $!`)
    # will now take the daemons with it. Normal function return leaves
    # them running, preserving the daemon-survives-terminal-close path.
    _install_signal_cleanup()

    # Wait for gateway to be ready
    time.sleep(2)

    # ── The Hatching Ceremony ──
    # Skip if already played during quickstart provisioning
    if not os.environ.get("_WINDYFLY_HATCHING_PLAYED"):
        from windyfly.hatching import play_hatching, show_ecosystem_status
        play_hatching(animate=True)
        show_ecosystem_status()
    else:
        # Still show ecosystem status even if ceremony already played
        from windyfly.hatching import show_ecosystem_status
        show_ecosystem_status()

    console.print("  [cyan]Brain log:[/cyan]    data/brain.log")
    console.print("  [cyan]Gateway log:[/cyan]  data/gateway.log")
    console.print()

    # Dashboard URL + auto-open browser
    dashboard_url = "http://localhost:3000"
    if gateway_pid:
        console.print(f"  [bold cyan]🌐 Dashboard:[/bold cyan]  {dashboard_url}")
        console.print("  [bold cyan]💬 Chat:[/bold cyan]       Open Windy Chat or visit the dashboard")
        console.print()
        agent_name = os.environ.get("WINDYFLY_AGENT_NAME", "Windy Fly")
        console.print(f"  Your agent [bold]{agent_name}[/bold] is waiting to chat with you.")
        console.print()
        no_browser = getattr(args, "no_browser", False)
        if not no_browser:
            try:
                webbrowser.open(dashboard_url)
            except Exception:
                pass

    if daemon:
        console.print("  [bold green]Windy Fly is running in the background.[/bold green]")
        console.print("  Run [bold]windy stop[/bold] to shut down.")
        console.print("  Run [bold]windy logs --follow[/bold] to watch logs.")
    else:
        console.print("  (Or type here to chat in the terminal)")
        console.print("  Run [bold]windy stop[/bold] to shut down.")
    console.print()

    # Open browser (unless --no-browser)
    if not getattr(args, "no_browser", False):
        try:
            webbrowser.open("http://localhost:3000")
        except Exception as e:
            logger.debug("Could not open browser: %s", e)


def cmd_stop(_args: argparse.Namespace) -> None:
    """Stop all Windy Fly processes."""
    pid_info = read_pid_file(PROJECT_ROOT)

    if pid_info is None:
        console.print("[dim]No PID file found. Nothing to stop.[/dim]")
        _do_kill_by_name()
        return

    stopped = 0

    for label, pid in [("Brain", pid_info.brain), ("Gateway", pid_info.gateway)]:
        if pid is None:
            continue
        try:
            if process_alive(pid):
                if process_terminate(pid):
                    console.print(f"  [green]✓[/green] Stopped {label} (PID {pid})")
                    stopped += 1
                else:
                    console.print(f"  [yellow]⚠ Could not stop {label} (PID {pid})[/yellow]")
            else:
                console.print(f"  [dim]{label} (PID {pid}) already stopped[/dim]")
        except (ValueError, OSError) as e:
            console.print(f"  [yellow]⚠ Could not stop {label} (PID {pid}): {e}[/yellow]")

    remove_pid_file(PROJECT_ROOT)

    if stopped:
        console.print(f"\n  [green]✓ Stopped {stopped} process(es)[/green]")
    else:
        console.print("\n  [dim]No running processes found[/dim]")
        _do_kill_by_name()


def cmd_status(_args: argparse.Namespace) -> None:
    """Show comprehensive agent status using the rich tree display."""
    from windyfly.cli_status import print_status
    print_status()


def cmd_setup_calendar(_args: argparse.Namespace) -> None:
    """One-time Google Calendar OAuth flow → activates calendar tools.

    Closes the pre-existing dead-end where the bot's graceful refusal
    text said "Run `windy setup-calendar`" but the subcommand didn't
    actually exist. ``setup_calendar_oauth`` has been in calendar.py
    since the tool was first written; this wires it to the CLI.

    Prereqs:
      1. Google Cloud project with the Calendar API enabled.
      2. ``calendar`` scope on the OAuth consent screen.
      3. Desktop-app OAuth Client ID downloaded as
         ``data/google_calendar_creds.json`` (or path in
         ``GOOGLE_CALENDAR_CREDENTIALS`` env var).

    Same scaffolding as ``windy setup-gmail`` — different scope, same
    Google Cloud project is fine.
    """
    console.print("[bold cyan]🪰 Setting up Google Calendar OAuth...[/bold cyan]")
    console.print()
    from windyfly.tools.calendar import setup_calendar_oauth
    ok = setup_calendar_oauth()
    if ok:
        console.print()
        console.print("[bold green]✓ Calendar connected.[/bold green]")
        console.print(
            "  Restart the bot to pick up the new token: "
            "[cyan]systemctl --user restart windy-0.service[/cyan]"
        )
    else:
        console.print()
        console.print("[bold red]✗ Calendar setup failed.[/bold red]")
        console.print(
            "  See the logged reason above. Common fixes:\n"
            "  • Download the Desktop-app OAuth client JSON from\n"
            "    Google Cloud Console → APIs & Services → Credentials\n"
            "  • Save it as data/google_calendar_creds.json (or set\n"
            "    GOOGLE_CALENDAR_CREDENTIALS to point at it)\n"
            "  • Add the calendar scope to your OAuth consent screen"
        )
        sys.exit(1)


def cmd_setup_gmail(_args: argparse.Namespace) -> None:
    """One-time Gmail OAuth flow → activates the email.send capability.

    Prereqs:
      1. Google Cloud project with the Gmail API enabled (the same
         project the calendar tool uses is fine).
      2. ``gmail.send`` scope added to the OAuth consent screen.
      3. Desktop-app OAuth Client ID downloaded as
         ``data/google_oauth_creds.json`` (or path in
         ``GOOGLE_OAUTH_CREDENTIALS`` env var).

    Opens a browser, captures the consent, writes
    ``data/gmail_token.json``. Restart the bot afterwards so the
    capability registers as ``configured=True``.
    """
    console.print("[bold cyan]🪰 Setting up Gmail OAuth...[/bold cyan]")
    console.print()
    from windyfly.agent.capabilities.email import setup_gmail_oauth
    ok = setup_gmail_oauth()
    if ok:
        console.print()
        console.print("[bold green]✓ Gmail connected.[/bold green]")
        console.print(
            "  Restart the bot to pick up the new token: "
            "[cyan]systemctl --user restart windy-0.service[/cyan]"
        )
    else:
        console.print()
        console.print("[bold red]✗ Gmail setup failed.[/bold red]")
        console.print(
            "  See the logged reason above. Common fixes:\n"
            "  • Download the Desktop-app OAuth client JSON from\n"
            "    Google Cloud Console → APIs & Services → Credentials\n"
            "  • Save it as data/google_oauth_creds.json (or set\n"
            "    GOOGLE_OAUTH_CREDENTIALS to point at it)\n"
            "  • Add the gmail.send scope to your OAuth consent screen"
        )
        sys.exit(1)


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
    except Exception as e:
        logger.debug("Gateway health check failed: %s", e)

    # Gateway not running — start it first
    gateway_dir = PROJECT_ROOT / "gateway"
    if not gateway_dir.exists() or not (gateway_dir / "src" / "server.ts").exists():
        console.print("[yellow]Gateway not available (pip install does not include it).[/yellow]")
        console.print("[dim]Use 'windy go' for quickstart, or clone the repo for the full dashboard.[/dim]")
        return

    import shutil
    if not shutil.which("bun"):
        console.print("[yellow]Bun not installed — gateway requires Bun to run.[/yellow]")
        console.print("[dim]Install: https://bun.sh[/dim]")
        return

    console.print("[bold cyan]🪰 Starting gateway for browser setup...[/bold cyan]")
    console.print()

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
# Lightweight handlers (delegate to other modules)
# ═══════════════════════════════════════════════════════════════════════


def _cmd_chat(args: argparse.Namespace) -> None:
    """Start CLI chat mode (alias for `windy start --cli`)."""
    args.cli = True
    args.no_browser = True
    cmd_start(args)


def _cmd_test(args: argparse.Namespace) -> None:
    """Run the agent self-test.

    --full also dispatches ecosystem health checks (Eternitas / Windy Pro /
    Matrix / Mail / Cloud) and exits non-zero if any *critical* dependency
    is red. Referenced from DEPLOY.md §5 and scripts/smoke-test.sh.
    """
    if getattr(args, "full", False):
        from windyfly.cli_selftest import run_full_self_test
        run_full_self_test(timeout=getattr(args, "timeout", 5.0))
    else:
        from windyfly.cli_selftest import run_self_test
        run_self_test()


def _cmd_repl(_args: argparse.Namespace) -> None:
    """Start the developer REPL."""
    from windyfly.commands import cmd_repl
    cmd_repl(_args)


def _cmd_kill(_args: argparse.Namespace) -> None:
    """Force kill everything — emergency stop."""
    from windyfly.platform import force_kill

    console.print("[bold red]Force-killing all Windy Fly processes...[/bold red]")
    console.print()

    pid_info = read_pid_file(PROJECT_ROOT)
    killed = 0

    if pid_info:
        for label, pid in [("Brain", pid_info.brain), ("Gateway", pid_info.gateway)]:
            if pid is not None and process_alive(pid):
                if force_kill(pid):
                    console.print(f"  [green]✓[/green] Killed {label} (PID {pid})")
                    killed += 1
        remove_pid_file(PROJECT_ROOT)

    # Also kill by name as fallback
    _do_kill_by_name()

    if killed:
        console.print(f"\n  [green]✓ Force-killed {killed} process(es)[/green]")
    else:
        console.print("\n  [dim]No running processes found (sent kill-by-name as fallback)[/dim]")


def _cmd_ps(_args: argparse.Namespace) -> None:
    """Show running Windy Fly processes."""
    from rich.table import Table

    pid_info = read_pid_file(PROJECT_ROOT)

    table = Table(title="Windy Fly Processes", border_style="cyan")
    table.add_column("Component", style="bold")
    table.add_column("PID", style="cyan")
    table.add_column("Status")
    table.add_column("Started", style="dim")

    if pid_info is None:
        console.print("[dim]No PID file found. Windy Fly may not be running.[/dim]")
        return

    started = pid_info.started or "unknown"

    for label, pid, alive in [
        ("Brain", pid_info.brain, pid_info.brain_alive),
        ("Gateway", pid_info.gateway, pid_info.gateway_alive),
    ]:
        pid_str = str(pid) if pid else "-"
        status = "[green]Running[/green]" if alive else "[red]Stopped[/red]"
        table.add_row(label, pid_str, status, started)

    console.print()
    console.print(table)
    console.print()


def _cmd_debug(_args: argparse.Namespace) -> None:
    """Show verbose debug information."""
    from windyfly.commands import cmd_debug
    cmd_debug(_args)


def _cmd_passport(_args: argparse.Namespace) -> None:
    """Show Eternitas passport."""
    from windyfly.commands import cmd_passport
    cmd_passport(_args)


def _cmd_keys(args: argparse.Namespace) -> None:
    """Manage the wk_ bot credential (show / rotate)."""
    from windyfly.commands.keys import cmd_keys
    cmd_keys(args)


def _cmd_mail(_args: argparse.Namespace) -> None:
    """Show mail status."""
    from windyfly.commands import cmd_mail
    cmd_mail(_args)


def _cmd_phone(_args: argparse.Namespace) -> None:
    """Show phone status."""
    from windyfly.commands import cmd_phone
    cmd_phone(_args)


def _cmd_cert(_args: argparse.Namespace) -> None:
    """Show birth certificate."""
    from windyfly.commands import cmd_cert
    cmd_cert(_args)


def _cmd_model(args: argparse.Namespace) -> None:
    """Show/change LLM model."""
    from windyfly.commands import cmd_model
    cmd_model(args)


def _cmd_soul(args: argparse.Namespace) -> None:
    """Show/edit personality."""
    from windyfly.commands import cmd_soul
    cmd_soul(args)


def _cmd_budget(args: argparse.Namespace) -> None:
    """Cost tracking."""
    from windyfly.commands import cmd_budget
    cmd_budget(args)


def _cmd_memory(args: argparse.Namespace) -> None:
    """Memory operations."""
    from windyfly.commands import cmd_memory
    cmd_memory(args)


def _cmd_skills(args: argparse.Namespace) -> None:
    """Skill management."""
    from windyfly.commands import cmd_skills
    cmd_skills(args)


def _cmd_export(_args: argparse.Namespace) -> None:
    """Backup everything."""
    from windyfly.commands import cmd_export
    cmd_export(_args)


def _cmd_import(args: argparse.Namespace) -> None:
    """Restore from backup."""
    from windyfly.commands import cmd_import
    cmd_import(args)


def _cmd_reset(args: argparse.Namespace) -> None:
    """Factory reset."""
    from windyfly.commands import cmd_reset
    cmd_reset(args)


# ═══════════════════════════════════════════════════════════════════════
# Ecosystem & channels (kept verbatim)
# ═══════════════════════════════════════════════════════════════════════


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
        except Exception:
            elapsed = (time.time() - start) * 1000
            table.add_row(name, url, "[red]FAIL[/red]", f"{elapsed:.0f}ms")

    console.print(table)
    console.print()


def _cmd_channels(_args: argparse.Namespace) -> None:
    """Show which messaging channels are configured."""
    import os

    from dotenv import load_dotenv
    from rich.tree import Tree

    load_dotenv(PROJECT_ROOT / ".env")

    tree = Tree("[bold cyan]Windy Fly Channels[/bold cyan]")

    channels = [
        ("CLI", None, None, "Always on"),
        ("Matrix", "MATRIX_BOT_TOKEN", "MATRIX_BOT_PASSWORD", "@windyfly:chat.windychat.ai"),
        ("Telegram", "TELEGRAM_BOT_TOKEN", None, "@BotFather token"),
        ("Discord", "DISCORD_BOT_TOKEN", None, "discord.com/developers"),
        ("Slack", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "api.slack.com/apps"),
        ("WhatsApp", "TWILIO_WHATSAPP_NUMBER", None, "Twilio WhatsApp API"),
        ("Signal", "SIGNAL_PHONE_NUMBER", None, "signal-cli-rest-api"),
        ("Teams", "TEAMS_APP_ID", None, "dev.teams.microsoft.com"),
        ("IRC", "IRC_SERVER", None, "No credentials needed"),
    ]

    for name, env1, env2, hint in channels:
        if env1 is None:
            # CLI — always active
            tree.add(f"[green]{name}[/green]  [green]Active[/green] [dim]({hint})[/dim]")
        elif os.environ.get(env1) or (env2 and os.environ.get(env2)):
            tree.add(f"[green]{name}[/green]  [green]Active[/green] [dim](token set)[/dim]")
        else:
            env_hint = env1
            tree.add(f"[dim]{name}[/dim]  [dim]Not configured[/dim] [dim](set {env_hint})[/dim]")

    console.print()
    console.print(tree)
    console.print()


def auto_detect_channels() -> list[str]:
    """Return names of channels that have credentials configured.

    Used by the channel manager to auto-register adapters.
    """
    import os

    detected = ["cli"]  # Always available

    if os.environ.get("MATRIX_BOT_TOKEN") or os.environ.get("MATRIX_BOT_PASSWORD"):
        detected.append("matrix")
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        detected.append("telegram")
    if os.environ.get("DISCORD_BOT_TOKEN"):
        detected.append("discord")
    if os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_APP_TOKEN"):
        detected.append("slack")
    if os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_WHATSAPP_NUMBER"):
        detected.append("whatsapp")
    if os.environ.get("SIGNAL_PHONE_NUMBER"):
        detected.append("signal")
    if os.environ.get("TEAMS_APP_ID"):
        detected.append("teams")
    if os.environ.get("IRC_SERVER"):
        detected.append("irc")

    return detected


# ═══════════════════════════════════════════════════════════════════════
# Help & commands display
# ═══════════════════════════════════════════════════════════════════════

# All commands organized by category for help display
_COMMAND_CATEGORIES = [
    ("Process Management", [
        ("go", "One-command quickstart"),
        ("start", "Start brain + gateway"),
        ("stop", "Stop all processes"),
        ("restart", "Stop + start"),
        ("kill", "Force kill everything (emergency)"),
        ("ps", "Show running processes"),
    ]),
    ("Chat & Interaction", [
        ("chat", "Start CLI chat mode (alias for start --cli)"),
        ("test", "Run self-test"),
        ("repl", "Developer REPL"),
    ]),
    ("Diagnostics", [
        ("doctor", "Full health check"),
        ("status", "Quick status summary"),
        ("logs", "Tail logs (brain/gateway)"),
        ("debug", "Verbose debug info"),
    ]),
    ("Identity & Ecosystem", [
        ("ecosystem", "Show ecosystem connections"),
        ("channels", "Show messaging channels"),
        ("passport", "Show Eternitas passport"),
        ("mail", "Show mail status"),
        ("phone", "Show phone status"),
        ("cert", "Show birth certificate"),
        ("keys", "Manage the wk_ bot credential (show, rotate)"),
    ]),
    ("Configuration", [
        ("config", "View/edit configuration (show, set, reset, path)"),
        ("model", "Show/change LLM model"),
        ("soul", "Show/edit personality"),
        ("budget", "Cost tracking"),
    ]),
    ("Memory & Skills", [
        ("memory", "Memory operations"),
        ("skills", "Skill management"),
    ]),
    ("Maintenance", [
        ("update", "Update to latest version"),
        ("version", "Show version info"),
        ("export", "Backup everything"),
        ("import", "Restore from backup"),
        ("reset", "Factory reset"),
    ]),
    ("Setup", [
        ("init", "Interactive setup wizard"),
        ("setup", "Browser-based setup wizard"),
    ]),
    ("Help", [
        ("help", "Show all commands grouped by category"),
        ("commands", "List all commands in compact table"),
    ]),
]


def _cmd_help(args: argparse.Namespace) -> None:
    """Show all commands grouped by category, or detailed help for one command."""
    from rich.panel import Panel

    cmd_name = getattr(args, "command_name", None)

    if cmd_name:
        # Show detailed help for a specific command
        # Find it in categories
        found = False
        for category, cmds in _COMMAND_CATEGORIES:
            for name, desc in cmds:
                if name == cmd_name:
                    console.print()
                    console.print(f"  [bold cyan]windy {name}[/bold cyan] — {desc}")
                    console.print(f"  [dim]Category: {category}[/dim]")
                    console.print()
                    found = True
                    break
            if found:
                break
        if not found:
            console.print(f"  [yellow]Unknown command: {cmd_name}[/yellow]")
            console.print("  Run [bold]windy help[/bold] to see all commands.")
        return

    # Show all commands grouped by category
    console.print()
    console.print(Panel(
        "[bold cyan]Windy Fly[/bold cyan] — Your AI. Your Rules. Your Ecosystem.",
        border_style="cyan",
    ))
    console.print()

    for category, cmds in _COMMAND_CATEGORIES:
        console.print(f"  [bold]{category}[/bold]")
        for name, desc in cmds:
            console.print(f"    [cyan]windy {name:<14}[/cyan] {desc}")
        console.print()

    console.print("  [dim]Run [bold]windy help <command>[/bold] for details on a specific command.[/dim]")
    console.print()


def _cmd_commands(_args: argparse.Namespace) -> None:
    """Show a compact table of all commands."""
    from rich.table import Table

    table = Table(
        title="Windy Fly Commands",
        border_style="cyan",
        show_lines=False,
    )
    table.add_column("Command", style="bold cyan", min_width=16)
    table.add_column("Description")
    table.add_column("Category", style="dim")

    for category, cmds in _COMMAND_CATEGORIES:
        for name, desc in cmds:
            table.add_row(f"windy {name}", desc, category)

    console.print()
    console.print(table)
    console.print()


# ═══════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════


def _cmd_install_service(_args: argparse.Namespace) -> None:
    """Install a system service so Windy Fly auto-starts on login.

    macOS: launchd plist in ~/Library/LaunchAgents/
    Linux: systemd user unit in ~/.config/systemd/user/
    """
    from windyfly.platform import IS_MAC, IS_LINUX, IS_WINDOWS

    if IS_WINDOWS:
        console.print("[yellow]Service install not yet supported on Windows.[/yellow]")
        console.print("[dim]Use Task Scheduler manually, or run 'windy start --daemon'.[/dim]")
        return

    import shutil

    uv_path = shutil.which("uv") or "/usr/local/bin/uv"
    brain_log = str(get_log_path(PROJECT_ROOT, "brain"))

    if IS_MAC:
        _install_launchd(uv_path, brain_log)
    elif IS_LINUX:
        _install_systemd(uv_path, brain_log)


def _install_launchd(uv_path: str, brain_log: str) -> None:
    """Install macOS launchd plist."""
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.windyfly.agent.plist"

    plist_content = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.windyfly.agent</string>

    <key>ProgramArguments</key>
    <array>
        <string>{uv_path}</string>
        <string>run</string>
        <string>python</string>
        <string>-m</string>
        <string>windyfly.bridge.uds_server</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{PROJECT_ROOT}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>{brain_log}</string>

    <key>StandardErrorPath</key>
    <string>{brain_log}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:{Path.home()}/.local/bin:{Path.home()}/.bun/bin</string>
    </dict>
</dict>
</plist>
"""

    plist_path.write_text(plist_content)
    console.print(f"  [green]✓[/green] Wrote {plist_path}")

    subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True)
    console.print("  [green]✓[/green] Service loaded — Windy Fly will auto-start on login")
    console.print()
    console.print(f"  [dim]Plist: {plist_path}[/dim]")
    console.print(f"  [dim]Logs:  {brain_log}[/dim]")
    console.print()
    console.print("  Run [bold]windy uninstall-service[/bold] to remove.")


def _install_systemd(uv_path: str, brain_log: str) -> None:
    """Install Linux systemd user service."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "windyfly.service"

    # Build EnvironmentFile line if .env exists
    env_file = PROJECT_ROOT / ".env"
    env_line = f"EnvironmentFile={env_file}" if env_file.exists() else ""

    unit_content = f"""\
[Unit]
Description=Windy Fly AI Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={PROJECT_ROOT}
ExecStart={uv_path} run python -m windyfly.bridge.uds_server
Restart=on-failure
RestartSec=5
StandardOutput=append:{brain_log}
StandardError=append:{brain_log}
{env_line}

# Generous shutdown timeout for graceful cleanup
TimeoutStopSec=15

[Install]
WantedBy=default.target
"""

    unit_path.write_text(unit_content)
    console.print(f"  [green]✓[/green] Wrote {unit_path}")

    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True,
    )
    console.print("  [green]✓[/green] Reloaded systemd user units")

    subprocess.run(
        ["systemctl", "--user", "enable", "windyfly"],
        capture_output=True,
    )
    console.print("  [green]✓[/green] Service enabled — Windy Fly will auto-start on login")
    console.print()
    console.print(f"  [dim]Unit:  {unit_path}[/dim]")
    console.print(f"  [dim]Logs:  {brain_log}[/dim]")
    console.print()
    console.print("  Run [bold]systemctl --user start windyfly[/bold] to start now.")
    console.print("  Run [bold]windy uninstall-service[/bold] to remove.")


def _cmd_uninstall_service(_args: argparse.Namespace) -> None:
    """Remove the auto-start service (launchd on macOS, systemd on Linux)."""
    from windyfly.platform import IS_MAC, IS_LINUX, IS_WINDOWS

    if IS_WINDOWS:
        console.print("[yellow]Service install not yet supported on Windows.[/yellow]")
        return

    if IS_MAC:
        plist_path = Path.home() / "Library" / "LaunchAgents" / "com.windyfly.agent.plist"
        if not plist_path.exists():
            console.print("[dim]No service installed. Nothing to remove.[/dim]")
            return
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
        plist_path.unlink(missing_ok=True)
        console.print("  [green]✓[/green] Service removed — Windy Fly will no longer auto-start")

    elif IS_LINUX:
        unit_path = Path.home() / ".config" / "systemd" / "user" / "windyfly.service"
        if not unit_path.exists():
            console.print("[dim]No service installed. Nothing to remove.[/dim]")
            return
        subprocess.run(["systemctl", "--user", "stop", "windyfly"], capture_output=True)
        subprocess.run(["systemctl", "--user", "disable", "windyfly"], capture_output=True)
        unit_path.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        console.print("  [green]✓[/green] Service stopped, disabled, and removed")


def main() -> None:
    """CLI entry point — registered as ``windy`` command via pyproject.toml."""
    parser = argparse.ArgumentParser(
        prog="windy",
        description="Windy Fly — Your AI. Your Rules. Your Ecosystem.",
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # ── Process Management ───────────────────────────────────────

    # windy go
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
    go_parser.add_argument(
        "--byok", action="store_true",
        help="Bring your own key — skip Windy Word managed-credential detection",
    )

    # windy start
    start_parser = sub.add_parser("start", help="Start brain + gateway")
    start_parser.add_argument(
        "--cli", action="store_true",
        help="Run in CLI chat mode (no gateway/dashboard)",
    )
    start_parser.add_argument(
        "--daemon", "-d", action="store_true",
        help="Run in background (survives terminal close)",
    )
    start_parser.add_argument(
        "--no-browser", action="store_true",
        help="Don't open browser after starting",
    )

    # windy stop
    sub.add_parser("stop", help="Stop all Windy Fly processes")

    # windy restart
    restart_parser = sub.add_parser("restart", help="Stop + start in one shot")
    restart_parser.add_argument(
        "--cli", action="store_true",
        help="Restart in CLI chat mode (no gateway/dashboard)",
    )
    restart_parser.add_argument(
        "--daemon", "-d", action="store_true",
        help="Restart in background (survives terminal close)",
    )
    restart_parser.add_argument(
        "--no-browser", action="store_true",
        help="Don't open browser after restarting",
    )

    # windy kill
    sub.add_parser("kill", help="Force kill everything (emergency)")

    # windy ps
    sub.add_parser("ps", help="Show running processes")

    # ── Chat & Interaction ───────────────────────────────────────

    # windy chat
    sub.add_parser("chat", help="Start CLI chat mode (alias for start --cli)")

    # windy test / windy selftest — same handler, shared flags. Keeping
    # both names makes smoke-test.sh (which calls `windy test`) and
    # DEPLOY.md (which documents `windy selftest --full`) both work.
    for test_name, help_text in (
        ("test",     "Run self-test to verify the agent works"),
        ("selftest", "Alias for `windy test`"),
    ):
        test_parser = sub.add_parser(test_name, help=help_text)
        test_parser.add_argument(
            "--full", action="store_true",
            help="Also check ecosystem health (Eternitas / Pro / Matrix / Mail / Cloud)",
        )
        test_parser.add_argument(
            "--timeout", type=float, default=5.0,
            help="Per-endpoint HTTP timeout in seconds (default: 5)",
        )

    # windy repl
    sub.add_parser("repl", help="Developer REPL")

    # ── Diagnostics ──────────────────────────────────────────────

    # windy doctor
    sub.add_parser("doctor", help="Full health check")

    # windy status
    sub.add_parser("status", help="Quick status summary")

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
    logs_parser.add_argument(
        "--brain", action="store_const", const="brain", dest="component",
        help="Shortcut for 'brain' component",
    )
    logs_parser.add_argument(
        "--gateway", action="store_const", const="gateway", dest="component",
        help="Shortcut for 'gateway' component",
    )

    # windy debug
    sub.add_parser("debug", help="Verbose debug info")

    # ── Identity & Ecosystem ─────────────────────────────────────

    # windy ecosystem
    sub.add_parser("ecosystem", help="Show ecosystem connections")

    # windy channels
    sub.add_parser("channels", help="Show messaging channels")

    # windy passport
    sub.add_parser("passport", help="Show Eternitas passport")

    # windy mail
    sub.add_parser("mail", help="Show mail status")

    # windy phone
    sub.add_parser("phone", help="Show phone status")

    # windy cert
    sub.add_parser("cert", help="Show birth certificate")

    # windy keys — manage the wk_ bot credential (rotate, inspect)
    keys_parser = sub.add_parser("keys", help="Manage the wk_ bot credential")
    keys_sub = keys_parser.add_subparsers(dest="action", help="Keys action")
    keys_sub.add_parser("show", help="Inspect the cached wk_ bot key")
    keys_rotate = keys_sub.add_parser("rotate", help="Mint a fresh wk_ key and revoke the old one")
    keys_rotate.add_argument(
        "--hard", action="store_true",
        help="Also cascade-revoke to Mail, Cloud, and Chat so they drop cached auth",
    )

    # ── Configuration ────────────────────────────────────────────

    # windy config
    config_parser = sub.add_parser("config", help="View/edit configuration")
    config_sub = config_parser.add_subparsers(dest="action", help="Config action")
    config_sub.add_parser("show", help="Display current configuration")
    config_set = config_sub.add_parser("set", help="Set a config value")
    config_set.add_argument("key", help="Key in section.name format (e.g., agent.default_model)")
    config_set.add_argument("value", help="New value")
    config_sub.add_parser("reset", help="Re-run the setup wizard")
    config_sub.add_parser("path", help="Show config file locations")

    # windy model
    model_parser = sub.add_parser("model", help="Show/change LLM model")
    model_sub = model_parser.add_subparsers(dest="action", help="Model action")
    model_sub.add_parser("list", help="List available models")
    model_set = model_sub.add_parser("set", help="Set the default model")
    model_set.add_argument("model_name", help="Model name (e.g., gpt-4o, claude-3-5-sonnet-latest)")
    model_sub.add_parser("test", help="Test the current model")

    # windy soul
    soul_parser = sub.add_parser("soul", help="Show/edit personality")
    soul_sub = soul_parser.add_subparsers(dest="action", help="Soul action")
    soul_sub.add_parser("edit", help="Edit personality interactively")
    soul_preset = soul_sub.add_parser("preset", help="Apply a personality preset")
    soul_preset.add_argument("name", help="Preset name (e.g., buddy, engineer, powerhouse)")
    soul_sub.add_parser("sliders", help="Adjust personality sliders")

    # windy budget
    budget_parser = sub.add_parser("budget", help="Cost tracking")
    budget_sub = budget_parser.add_subparsers(dest="action", help="Budget action")
    budget_sub.add_parser("month", help="Show this month's costs")
    budget_sub.add_parser("history", help="Show cost history")
    budget_set = budget_sub.add_parser("set", help="Set monthly budget")
    budget_set.add_argument("amount", type=float, help="Monthly budget amount in USD")

    # ── Memory & Skills ──────────────────────────────────────────

    # windy memory
    memory_parser = sub.add_parser("memory", help="Memory operations")
    memory_sub = memory_parser.add_subparsers(dest="action", help="Memory action")
    memory_sub.add_parser("stats", help="Show memory statistics")
    memory_search = memory_sub.add_parser("search", help="Search memory")
    memory_search.add_argument("query", help="Search query")
    memory_sub.add_parser("nodes", help="Show memory nodes")
    memory_sub.add_parser("intents", help="Show learned intents")
    memory_sub.add_parser("export", help="Export memory")
    memory_clear = memory_sub.add_parser("clear", help="Clear all memory")
    memory_clear.add_argument("--confirm", action="store_true", required=True, help="Confirm clearing memory")

    # windy skills
    skills_parser = sub.add_parser("skills", help="Skill management")
    skills_sub = skills_parser.add_subparsers(dest="action", help="Skills action")
    skills_sub.add_parser("all", help="List all skills")
    skills_run = skills_sub.add_parser("run", help="Run a skill")
    skills_run.add_argument("name", help="Skill name")
    skills_eval = skills_sub.add_parser("eval", help="Evaluate a skill")
    skills_eval.add_argument("name", help="Skill name")

    # ── Maintenance ──────────────────────────────────────────────

    # windy update
    sub.add_parser("update", help="Update to latest version")

    # windy rollback
    rollback_parser = sub.add_parser("rollback", help="Rollback to a specific version")
    rollback_parser.add_argument("version", nargs="?", help="Version to rollback to (e.g. 0.5.0)")

    # windy version
    sub.add_parser("version", help="Show version info")

    # windy export
    sub.add_parser("export", help="Backup everything")

    # windy import
    import_parser = sub.add_parser("import", help="Restore from backup")
    import_parser.add_argument("file", help="Path to backup file")

    # windy reset
    reset_parser = sub.add_parser("reset", help="Factory reset")
    reset_group = reset_parser.add_mutually_exclusive_group()
    reset_group.add_argument("--soft", action="store_true", help="Soft reset (keep data, reset config)")
    reset_group.add_argument("--hard", action="store_true", help="Hard reset (delete everything)")

    # ── Setup ────────────────────────────────────────────────────

    # windy init
    sub.add_parser("init", help="Interactive setup wizard")

    # windy setup
    sub.add_parser("setup", help="Browser-based setup wizard")

    # windy setup-calendar
    sub.add_parser(
        "setup-calendar",
        help="One-time Google Calendar OAuth flow (activates calendar tools)",
    )

    # windy setup-gmail
    sub.add_parser(
        "setup-gmail",
        help="One-time Gmail OAuth flow (activates email.send capability)",
    )

    # ── Help ─────────────────────────────────────────────────────

    # windy help
    help_parser = sub.add_parser("help", help="Show all commands grouped by category")
    help_parser.add_argument("command_name", nargs="?", default=None, help="Command to get help for")

    # windy commands
    sub.add_parser("commands", help="List all commands in compact table")

    # windy install-service (macOS launchd)
    sub.add_parser("install-service", help="Auto-start on login (macOS launchd)")

    # windy uninstall-service
    sub.add_parser("uninstall-service", help="Remove auto-start service")

    # ── Parse and dispatch ───────────────────────────────────────

    args = parser.parse_args()

    if args.command is None:
        # No subcommand — run quickstart if not configured, else show help
        env_file = PROJECT_ROOT / ".env"
        config_file = PROJECT_ROOT / "windyfly.toml"
        if not env_file.exists() or not config_file.exists():
            from windyfly.quickstart import cmd_go
            cmd_go(args)
        else:
            _cmd_help(argparse.Namespace(command_name=None))
        return

    # Lazy imports for commands from commands.py
    def _get_cmd_restart(a):
        from windyfly.commands import cmd_restart
        cmd_restart(a)

    def _get_cmd_doctor(a):
        from windyfly.commands import cmd_doctor
        cmd_doctor(a)

    def _get_cmd_update(a):
        from windyfly.commands import cmd_update
        cmd_update(a)

    def _get_cmd_rollback(a):
        from windyfly.commands._legacy import cmd_rollback
        cmd_rollback(a)

    def _get_cmd_logs(a):
        from windyfly.commands import cmd_logs
        cmd_logs(a)

    def _get_cmd_config(a):
        from windyfly.commands import cmd_config
        cmd_config(a)

    def _get_cmd_version(a):
        from windyfly.commands import cmd_version
        cmd_version(a)

    def _get_cmd_go(a):
        from windyfly.quickstart import cmd_go
        cmd_go(a)

    # Dispatch table
    commands = {
        # Process Management
        "go": _get_cmd_go,
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": _get_cmd_restart,
        "kill": _cmd_kill,
        "ps": _cmd_ps,
        # Chat & Interaction
        "chat": _cmd_chat,
        "test": _cmd_test,
        "selftest": _cmd_test,
        "repl": _cmd_repl,
        # Diagnostics
        "doctor": _get_cmd_doctor,
        "status": cmd_status,
        "logs": _get_cmd_logs,
        "debug": _cmd_debug,
        # Identity & Ecosystem
        "ecosystem": _cmd_ecosystem,
        "channels": _cmd_channels,
        "passport": _cmd_passport,
        "keys": _cmd_keys,
        "mail": _cmd_mail,
        "phone": _cmd_phone,
        "cert": _cmd_cert,
        # Configuration
        "config": _get_cmd_config,
        "model": _cmd_model,
        "soul": _cmd_soul,
        "budget": _cmd_budget,
        # Memory & Skills
        "memory": _cmd_memory,
        "skills": _cmd_skills,
        # Maintenance
        "update": _get_cmd_update,
        "rollback": _get_cmd_rollback,
        "version": _get_cmd_version,
        "export": _cmd_export,
        "import": _cmd_import,
        "reset": _cmd_reset,
        # Setup
        "init": cmd_init,
        "setup": cmd_setup,
        "setup-calendar": cmd_setup_calendar,
        "setup-gmail": cmd_setup_gmail,
        # Help
        "help": _cmd_help,
        "commands": _cmd_commands,
        # Service
        "install-service": _cmd_install_service,
        "uninstall-service": _cmd_uninstall_service,
    }

    handler = commands.get(args.command)
    if handler:
        try:
            handler(args)
        except KeyboardInterrupt:
            console.print("\n  [dim]Interrupted. Goodbye![/dim]")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
