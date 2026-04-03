"""``windy status`` — rich tree-formatted status display.

Reads all values from the database, config, and environment to present
a comprehensive snapshot of the running Windy Fly agent.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.tree import Tree

logger = logging.getLogger(__name__)
console = Console()


def get_version() -> str:
    """Return the Windy Fly version from pyproject.toml or fallback."""
    try:
        from importlib.metadata import version
        return version("windyfly")
    except Exception as e:
        logger.debug("Version lookup failed: %s", e)
        return "0.1.0"


def _fmt_phone(raw: str) -> str:
    """Format a phone number nicely: +15551234567 → +1 (555) 123-4567."""
    if not raw or len(raw) < 10:
        return raw or "not configured"
    digits = raw.lstrip("+")
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits[0]} ({digits[1:4]}) {digits[4:7]}-{digits[7:11]}"
    return raw


def _fmt_bytes(size_bytes: int) -> str:
    """Format bytes into human-readable form."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _fmt_uptime(start_time: float | None) -> str:
    """Format uptime from a start timestamp."""
    if not start_time:
        return "unknown"
    elapsed = time.time() - start_time
    if elapsed < 60:
        return f"{int(elapsed)}s"
    elif elapsed < 3600:
        return f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
    else:
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        return f"{hours}h {minutes}m"


def _safe_get(d: dict, *keys: str, default: Any = None) -> Any:
    """Safely traverse nested dicts."""
    current = d
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, default)
        else:
            return default
    return current


def print_status(config: dict[str, Any] | None = None) -> None:
    """Print the full Windy Fly status tree.

    Reads from config, database, and environment. Handles all values
    gracefully if they are missing or unconfigured.

    Args:
        config: Optional config dict. If None, attempts to load from windyfly.toml.
    """
    # Load config if not provided
    if config is None:
        try:
            from windyfly.config import load_config
            config = load_config()
        except Exception as e:
            logger.debug("Config load failed: %s", e)
            config = {}

    # Database info
    db_path = _safe_get(config, "memory", "db_path", default="data/windyfly.db")
    db = None
    db_size_str = "N/A"
    node_count = 0
    episode_count = 0
    active_intents = 0
    promoted_skills = 0
    total_skills = 0
    unresolved_failures = 0
    daily_spend = 0.0
    daily_budget = _safe_get(config, "costs", "daily_budget_usd", default=5.0)
    preset = _safe_get(config, "personality", "preset", default="unknown")
    warmth = _safe_get(config, "personality", "warmth", default="?")
    humor = _safe_get(config, "personality", "humor_level", default="?")

    try:
        from windyfly.memory.database import Database
        if Path(db_path).exists():
            db = Database(db_path)
            db_size = Path(db_path).stat().st_size
            db_size_str = f"{_fmt_bytes(db_size)} ({db_path})"

            # Memory stats
            row = db.fetchone("SELECT COUNT(*) as c FROM nodes")
            node_count = row["c"] if row else 0

            row = db.fetchone("SELECT COUNT(*) as c FROM episodes")
            episode_count = row["c"] if row else 0

            row = db.fetchone("SELECT COUNT(*) as c FROM intents WHERE status = 'active'")
            active_intents = row["c"] if row else 0

            # Skills counts
            row = db.fetchone("SELECT COUNT(*) as c FROM skills WHERE promoted = TRUE")
            promoted_skills = row["c"] if row else 0
            row = db.fetchone("SELECT COUNT(*) as c FROM skills")
            total_skills = row["c"] if row else 0

            # Unresolved failures
            row = db.fetchone("SELECT COUNT(*) as c FROM failures WHERE resolved_at IS NULL")
            unresolved_failures = row["c"] if row else 0

            # Cost tracking
            row = db.fetchone(
                "SELECT COALESCE(SUM(cost_usd), 0) as total FROM cost_ledger "
                "WHERE created_at > datetime('now', '-1 day')"
            )
            daily_spend = round(row["total"], 2) if row else 0.0

            # Personality from DB (overrides config)
            from windyfly.memory.soul import get_soul
            warmth_row = get_soul(db, "slider_warmth")
            humor_row = get_soul(db, "slider_humor")
            if warmth_row:
                warmth = warmth_row["value"]
            if humor_row:
                humor = humor_row["value"]
        else:
            db_size_str = f"not found ({db_path})"
    except Exception as e:
        db_size_str = f"error ({e})"

    # Agent info
    agent_name = _safe_get(config, "agent", "name", default="Windy Fly")
    version = get_version()
    model = _safe_get(config, "agent", "default_model", default=os.environ.get("DEFAULT_MODEL", "not configured"))

    # Matrix
    matrix_homeserver = _safe_get(config, "matrix", "homeserver", default="not configured")
    matrix_status = "disconnected"
    room_count = 0
    if matrix_homeserver and matrix_homeserver != "not configured":
        has_token = bool(os.environ.get("MATRIX_BOT_TOKEN"))
        has_password = bool(os.environ.get("MATRIX_BOT_PASSWORD"))
        if has_token or has_password:
            host = matrix_homeserver.replace("https://", "").replace("http://", "")
            matrix_status = f"configured for {host}"
            try:
                import httpx
                r = httpx.get("http://localhost:3000/api/health", timeout=1)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("brain_connected"):
                        matrix_status = f"connected ({host})"
                        room_count = data.get("room_count", 0)
            except Exception as e:
                logger.debug("Matrix health check failed: %s", e)
        else:
            matrix_status = "credentials not set"
    else:
        matrix_status = "not configured"

    # Mail
    email = os.environ.get("AGENT_EMAIL", "")
    if not email:
        try:
            if db:
                row = db.fetchone("SELECT value FROM soul WHERE key = 'email_address'")
                if row:
                    email = row["value"]
        except Exception as e:
            logger.debug("Email lookup failed: %s", e)
    email_status = f"{email} (active)" if email else "not configured"

    # Phone
    phone = os.environ.get("AGENT_PHONE", os.environ.get("TWILIO_PHONE_NUMBER", ""))
    phone_display = f"{_fmt_phone(phone)} (active)" if phone else "not configured"

    # Eternitas
    passport = os.environ.get("ETERNITAS_PASSPORT", "")
    trust_score = "?"
    if passport and db:
        try:
            from windyfly.eternitas.provision import get_eternitas_client
            client = get_eternitas_client(db=db)
            p = client.lookup(passport)
            if p:
                trust_score = str(getattr(p, "trust_score", "?"))
        except Exception as e:
            logger.debug("Trust score lookup failed: %s", e)
    eternitas_status = f"{passport} (trust: {trust_score}/100)" if passport else "not configured"

    # Uptime — check PID file
    uptime_str = "not running"
    try:
        from windyfly.platform import get_pid_path
        project_root = Path(__file__).resolve().parent.parent.parent
        pid_file = get_pid_path(project_root)
        if pid_file.exists():
            from windyfly.platform import process_alive
            pids = pid_file.read_text().strip().split("\n")
            alive = [p for p in pids if process_alive(int(p.strip()))]
            if alive:
                pid_creation = pid_file.stat().st_mtime
                uptime_str = _fmt_uptime(pid_creation)
            else:
                uptime_str = "not running"
    except Exception as e:
        logger.debug("Uptime check failed: %s", e)
        uptime_str = "unknown"

    # ── Build the tree ────────────────────────────────────────────

    tree = Tree(
        "[bold cyan]🪰 Windy Fly Status[/bold cyan]",
        guide_style="cyan",
    )

    tree.add(f"[bold]Agent:[/bold] {agent_name} v{version}")
    tree.add(f"[bold]Model:[/bold] {model} (${daily_spend:.2f}/${daily_budget:.2f} today)")
    tree.add(f"[bold]Memory:[/bold] {node_count} nodes, {episode_count} episodes, {active_intents} active intents")
    tree.add(f"[bold]Personality:[/bold] {preset} (warmth={warmth}, humor={humor})")
    tree.add(f"[bold]Skills:[/bold] {promoted_skills} promoted, {total_skills} total")
    tree.add(f"[bold]Matrix:[/bold] {matrix_status} ({room_count} rooms)")
    tree.add(f"[bold]Mail:[/bold] {email_status}")
    tree.add(f"[bold]Phone:[/bold] {phone_display}")
    tree.add(f"[bold]Eternitas:[/bold] {eternitas_status}")
    tree.add(f"[bold]Database:[/bold] {db_size_str}")
    tree.add(f"[bold]Failures:[/bold] {unresolved_failures} unresolved")
    tree.add(f"[bold]Uptime:[/bold] {uptime_str}")

    console.print()
    console.print(tree)
    console.print()

    if db:
        db.close()
