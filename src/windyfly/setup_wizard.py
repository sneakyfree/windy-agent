"""Windy Fly Setup Wizard — Rich TUI interactive setup.

Guides a new user through API key entry, model selection, personality
preset, and config generation.  Run via ``windy init`` or directly::

    uv run python -m windyfly.setup_wizard
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)
console = Console()

# ── Paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = get_project_root()
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
ENV_FILE = PROJECT_ROOT / ".env"
CONFIG_FILE = PROJECT_ROOT / "windyfly.toml"
DATA_DIR = PROJECT_ROOT / "data"

# ── Provider metadata ──────────────────────────────────────────────────
PROVIDERS: list[dict[str, str]] = [
    {
        "key": "OPENAI_API_KEY",
        "name": "OpenAI",
        "hint": "Starts with sk-...",
        "models": "gpt-4o, gpt-4o-mini, o3-mini",
        "url": "https://platform.openai.com/api-keys",
    },
    {
        "key": "ANTHROPIC_API_KEY",
        "name": "Anthropic",
        "hint": "Starts with sk-ant-...",
        "models": "claude-3-5-sonnet, claude-3-5-haiku, claude-4-opus",
        "url": "https://console.anthropic.com/settings/keys",
    },
    {
        "key": "GROK_API_KEY",
        "name": "xAI Grok",
        "hint": "xai-...",
        "models": "grok-3, grok-3-mini",
        "url": "https://console.x.ai",
    },
    {
        "key": "GEMINI_API_KEY",
        "name": "Google Gemini",
        "hint": "AIza...",
        "models": "gemini-2.5-pro, gemini-2.5-flash",
        "url": "https://aistudio.google.com/apikey",
    },
    {
        "key": "DEEPSEEK_API_KEY",
        "name": "DeepSeek",
        "hint": "sk-...",
        "models": "deepseek-chat, deepseek-reasoner",
        "url": "https://platform.deepseek.com",
    },
    {
        "key": "MISTRAL_API_KEY",
        "name": "Mistral",
        "hint": "...",
        "models": "mistral-large, mistral-small",
        "url": "https://console.mistral.ai/api-keys",
    },
]

# ── Model choices per provider ─────────────────────────────────────────
MODEL_OPTIONS: list[dict[str, str]] = [
    {"id": "gpt-4o-mini", "provider": "OpenAI", "tier": "💚 Budget-Friendly", "desc": "Fast, cheap, surprisingly capable. Great default."},
    {"id": "gpt-4o", "provider": "OpenAI", "tier": "💛 Balanced", "desc": "Full-power GPT-4o. Excellent reasoning."},
    {"id": "claude-3-5-sonnet-latest", "provider": "Anthropic", "tier": "💛 Balanced", "desc": "Best all-rounder. Excellent at coding and conversation."},
    {"id": "claude-3-5-haiku-latest", "provider": "Anthropic", "tier": "💚 Budget-Friendly", "desc": "Ultra-fast Anthropic model. Great for quick responses."},
    {"id": "grok-3-mini", "provider": "xAI Grok", "tier": "💚 Budget-Friendly", "desc": "Fast, unfiltered reasoning. Think-outside-the-box vibes."},
    {"id": "grok-3", "provider": "xAI Grok", "tier": "🔥 Premium", "desc": "xAI's flagship. Excellent reasoning, witty responses."},
    {"id": "deepseek-chat", "provider": "DeepSeek", "tier": "💚 Budget-Friendly", "desc": "Extremely cheap. Great for high-volume use."},
    {"id": "gemini-2.5-flash", "provider": "Google Gemini", "tier": "💚 Budget-Friendly", "desc": "Google's fast model. Free tier available."},
]

# ── Personality presets ────────────────────────────────────────────────
PRESETS: dict[str, dict[str, Any]] = {
    "buddy": {
        "emoji": "🤝",
        "tagline": "Your warm, witty best friend who remembers everything",
        "personality": 8, "humor": 7, "warmth": 9, "formality": 3,
    },
    "engineer": {
        "emoji": "🔧",
        "tagline": "Precise, technical, no-nonsense. Gets the job done.",
        "personality": 4, "humor": 2, "warmth": 4, "formality": 7,
    },
    "powerhouse": {
        "emoji": "⚡",
        "tagline": "Maximum everything. Deep reasoning, long context, full power.",
        "personality": 7, "humor": 5, "warmth": 6, "formality": 5,
    },
    "coder": {
        "emoji": "💻",
        "tagline": "Optimized for code. Terse answers, deep tool use.",
        "personality": 3, "humor": 2, "warmth": 3, "formality": 5,
    },
    "friend": {
        "emoji": "💛",
        "tagline": "Emotionally attuned, supportive, great listener.",
        "personality": 9, "humor": 5, "warmth": 10, "formality": 2,
    },
    "writer": {
        "emoji": "✍️",
        "tagline": "Creative, eloquent, imaginative. Born to write.",
        "personality": 8, "humor": 6, "warmth": 7, "formality": 6,
    },
    "researcher": {
        "emoji": "🔬",
        "tagline": "Cites sources, reasons deeply, never guesses.",
        "personality": 4, "humor": 1, "warmth": 3, "formality": 8,
    },
    "silent": {
        "emoji": "🤫",
        "tagline": "Minimal output. Maximum efficiency. Cheapest mode.",
        "personality": 1, "humor": 0, "warmth": 2, "formality": 5,
    },
}


# ═══════════════════════════════════════════════════════════════════════
# Wizard steps
# ═══════════════════════════════════════════════════════════════════════


def _banner() -> None:
    """Show the welcome banner."""
    banner = Text()
    banner.append("🪰 ", style="bold")
    banner.append("Windy Fly Setup", style="bold cyan")
    banner.append(" — ", style="dim")
    banner.append("Your AI. Your Rules. Your Ecosystem.", style="italic")

    console.print()
    console.print(
        Panel(
            banner,
            border_style="cyan",
            padding=(1, 4),
            subtitle="[dim]v0.1.0 · windyfly.com[/dim]",
        )
    )
    console.print()


def _check_prerequisites() -> dict[str, bool]:
    """Check for required tools and report status."""
    from windyfly.platform import IS_WINDOWS, diagnose

    console.print("[bold]Step 0 of 4[/bold] · [cyan]Checking prerequisites...[/cyan]")
    console.print()

    report = diagnose()
    checks: dict[str, bool] = {}

    # Python
    py_ok = sys.version_info >= (3, 12)
    checks["python"] = py_ok
    status = "✅" if py_ok else "❌"
    console.print(f"  {status} Python {report.python_version}" + ("" if py_ok else " [red](need 3.12+)[/red]"))

    # uv
    checks["uv"] = report.has_uv
    if report.has_uv:
        console.print(f"  ✅ uv [dim]({shutil.which('uv')})[/dim]")
    else:
        hint = "https://docs.astral.sh/uv/" if IS_WINDOWS else "curl -LsSf https://astral.sh/uv/install.sh | sh"
        console.print(f"  ❌ uv [red](not found — install: {hint})[/red]")

    # Bun
    checks["bun"] = report.has_bun
    if report.has_bun:
        console.print(f"  ✅ Bun [dim]({shutil.which('bun')})[/dim]")
    else:
        hint = "https://bun.sh" if IS_WINDOWS else "curl -fsSL https://bun.sh/install | bash"
        console.print(f"  ❌ Bun [red](not found — install: {hint})[/red]")

    # Platform info
    console.print(f"  ℹ️  Platform: {report.system} — IPC mode: {report.ipc_mode}")

    console.print()

    if not all(checks.values()):
        missing = [k for k, v in checks.items() if not v]
        console.print(f"[yellow]⚠ Missing: {', '.join(missing)}[/yellow]")
        if Confirm.ask("  Attempt to auto-install missing tools?", default=True):
            _auto_install(checks)
        else:
            console.print("[red]Cannot continue without all prerequisites.[/red]")
            sys.exit(1)

    return checks


def _auto_install(checks: dict[str, bool]) -> None:
    """Try to install missing prerequisites (platform-aware)."""
    from windyfly.platform import IS_WINDOWS

    if not checks.get("uv"):
        console.print("  [cyan]Installing uv...[/cyan]")
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["powershell", "-ExecutionPolicy", "ByPass", "-c",
                     "irm https://astral.sh/uv/install.ps1 | iex"],
                    check=True,
                    capture_output=True,
                )
            else:
                subprocess.run(
                    ["bash", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
                    check=True,
                    capture_output=True,
                )
            console.print("  ✅ uv installed")
        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print("  [red]❌ Failed to install uv. Install manually: https://docs.astral.sh/uv/[/red]")
            sys.exit(1)

    if not checks.get("bun"):
        console.print("  [cyan]Installing Bun...[/cyan]")
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["powershell", "-ExecutionPolicy", "ByPass", "-c",
                     "irm https://bun.sh/install.ps1 | iex"],
                    check=True,
                    capture_output=True,
                )
            else:
                subprocess.run(
                    ["bash", "-c", "curl -fsSL https://bun.sh/install | bash"],
                    check=True,
                    capture_output=True,
                )
            console.print("  ✅ Bun installed")
        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print("  [red]❌ Failed to install Bun. Install manually: https://bun.sh[/red]")
            sys.exit(1)

    console.print()


def _step_api_keys() -> dict[str, str]:
    """Step 1: Collect API keys from the user."""
    console.print("[bold]Step 1 of 4[/bold] · [cyan]Connect your AI providers[/cyan]")
    console.print("[dim]  You need at least one API key. Skip any you don't have.[/dim]")
    console.print()

    collected: dict[str, str] = {}

    for provider in PROVIDERS:
        # Check if already in environment
        existing = os.environ.get(provider["key"], "")
        if existing and len(existing) > 8:
            masked = existing[:8] + "..." + existing[-4:]
            console.print(f"  [green]✓[/green] {provider['name']}: [dim]{masked} (from environment)[/dim]")
            collected[provider["key"]] = existing
            continue

        console.print(f"  [bold]{provider['name']}[/bold] — {provider['models']}")
        console.print(f"    [dim]Get your key: {provider['url']}[/dim]")

        key = Prompt.ask(
            f"    Paste {provider['name']} API key [dim](or Enter to skip)[/dim]",
            default="",
            show_default=False,
        )

        if key.strip():
            collected[provider["key"]] = key.strip()
            # Validate the key
            valid = _validate_key(provider["key"], key.strip())
            if valid:
                console.print("    [green]✓ Valid![/green]")
            else:
                console.print("    [yellow]⚠ Couldn't validate (saved anyway — may work)[/yellow]")
        else:
            console.print("    [dim]Skipped[/dim]")

        console.print()

    if not collected:
        console.print("[red]❌ You need at least one API key to continue.[/red]")
        sys.exit(1)

    console.print(f"  [green]✓ {len(collected)} provider(s) configured[/green]")
    console.print()
    return collected


def _validate_key(key_name: str, key_value: str) -> bool:
    """Quick validation of an API key by making a lightweight API call."""
    try:
        import httpx

        if key_name == "OPENAI_API_KEY":
            r = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key_value}"},
                timeout=10,
            )
            return r.status_code == 200

        if key_name == "ANTHROPIC_API_KEY":
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key_value,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-3-5-haiku-latest",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=10,
            )
            return r.status_code in (200, 400)  # 400 = valid key but bad request is fine

        if key_name == "GROK_API_KEY":
            r = httpx.get(
                "https://api.x.ai/v1/models",
                headers={"Authorization": f"Bearer {key_value}"},
                timeout=10,
            )
            return r.status_code == 200

        # For other providers, just check basic format
        return len(key_value) > 10

    except Exception as e:
        logger.debug("API key validation failed: %s", e)
        return False


def _step_model(api_keys: dict[str, str]) -> str:
    """Step 2: Choose a default model."""
    console.print("[bold]Step 2 of 4[/bold] · [cyan]Pick your default model[/cyan]")
    console.print("[dim]  You can change this anytime from the Sliders tab.[/dim]")
    console.print()

    # Filter to models from providers the user has keys for
    provider_map = {
        "OPENAI_API_KEY": "OpenAI",
        "ANTHROPIC_API_KEY": "Anthropic",
        "GROK_API_KEY": "xAI Grok",
        "DEEPSEEK_API_KEY": "DeepSeek",
        "GEMINI_API_KEY": "Google Gemini",
        "MISTRAL_API_KEY": "Mistral",
    }
    available_providers = {provider_map[k] for k in api_keys if k in provider_map}
    available_models = [m for m in MODEL_OPTIONS if m["provider"] in available_providers]

    if not available_models:
        console.print("[yellow]No models available for your providers. Using gpt-4o-mini.[/yellow]")
        return "gpt-4o-mini"

    table = Table(show_header=True, header_style="bold cyan", border_style="dim")
    table.add_column("#", style="bold", width=3)
    table.add_column("Model", style="green")
    table.add_column("Provider", style="dim")
    table.add_column("Tier")
    table.add_column("Description", style="dim")

    for i, model in enumerate(available_models, 1):
        table.add_row(str(i), model["id"], model["provider"], model["tier"], model["desc"])

    console.print(table)
    console.print()

    choice = Prompt.ask(
        "  Pick a model [dim](number)[/dim]",
        default="1",
    )

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(available_models):
            selected = available_models[idx]["id"]
        else:
            selected = available_models[0]["id"]
    except ValueError:
        # User typed a model name directly
        selected = choice.strip() if choice.strip() else available_models[0]["id"]

    console.print(f"  [green]✓ Default model: {selected}[/green]")
    console.print()
    return selected


def _step_personality() -> str:
    """Step 3: Choose a personality preset."""
    console.print("[bold]Step 3 of 4[/bold] · [cyan]Choose a personality preset[/cyan]")
    console.print("[dim]  This sets your agent's default vibe. Adjust individual sliders later.[/dim]")
    console.print()

    table = Table(show_header=True, header_style="bold cyan", border_style="dim")
    table.add_column("#", style="bold", width=3)
    table.add_column("Preset", style="green")
    table.add_column("", width=3)
    table.add_column("Vibe", style="dim")

    preset_names = list(PRESETS.keys())
    for i, name in enumerate(preset_names, 1):
        preset = PRESETS[name]
        table.add_row(str(i), name, preset["emoji"], preset["tagline"])

    console.print(table)
    console.print()

    choice = Prompt.ask(
        "  Pick a preset [dim](number or name)[/dim]",
        default="1",
    )

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(preset_names):
            selected = preset_names[idx]
        else:
            selected = "buddy"
    except ValueError:
        selected = choice.strip().lower() if choice.strip().lower() in PRESETS else "buddy"

    preset = PRESETS[selected]
    console.print(f"  [green]✓ Preset: {preset['emoji']} {selected}[/green] — {preset['tagline']}")
    console.print()
    return selected


def _step_finalize(
    api_keys: dict[str, str],
    model: str,
    preset: str,
) -> None:
    """Step 4: Write config files and finalize."""
    console.print("[bold]Step 4 of 4[/bold] · [cyan]Writing configuration...[/cyan]")
    console.print()

    # Ensure data directory exists
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Write .env
    env_lines = [
        "# Windy Fly — generated by `windy init`",
        f"DEFAULT_MODEL={model}",
        "",
        "# LLM Providers",
    ]
    for provider in PROVIDERS:
        value = api_keys.get(provider["key"], "")
        env_lines.append(f"{provider['key']}={value}")

    env_lines.extend([
        "",
        "# Database",
        "WINDYFLY_DB_PATH=data/windyfly.db",
        "",
        "# Logging",
        "LOG_LEVEL=INFO",
        "",
        "# Matrix / Windy Chat (optional)",
        "MATRIX_HOMESERVER=https://chat.windyword.ai",
        "MATRIX_BOT_USER=@windyfly:chat.windyword.ai",
        "MATRIX_BOT_TOKEN=",
        "MATRIX_BOT_PASSWORD=",
        "",
        "# Windy Pro API (optional)",
        "WINDY_API_URL=http://localhost:8098",
        "WINDY_JWT=",
    ])

    ENV_FILE.write_text("\n".join(env_lines) + "\n")
    console.print("  [green]✓[/green] .env written")

    # Write windyfly.toml with personality from preset
    preset_data = PRESETS[preset]
    toml_content = f"""[agent]
name = "Windy Fly"
default_model = "{model}"
max_context_tokens = 8000
max_response_tokens = 2000
temperature = 0.7

[memory]
db_path = "data/windyfly.db"
max_episodes_per_context = 20
max_nodes_per_context = 10

[personality]
soul_path = "SOUL.md"
preset = "{preset}"
humor_level = {preset_data.get('humor', 5)}
formality = {preset_data.get('formality', 5)}
proactivity = 5
verbosity = 5
reasoning_depth = 6
autonomy = 3
epistemic_strictness = 5
warmth = {preset_data.get('warmth', 5)}

[costs]
daily_budget_usd = 5.0
warn_at_usd = 0.50

[matrix]
homeserver = "https://chat.windyword.ai"
bot_user = "@windyfly:chat.windyword.ai"

[windy_api]
base_url = "http://localhost:8098"
"""
    CONFIG_FILE.write_text(toml_content)
    console.print(f"  [green]✓[/green] windyfly.toml written [dim](preset: {preset})[/dim]")

    # Install Python deps
    console.print("  [cyan]Installing Python dependencies...[/cyan]")
    try:
        subprocess.run(
            ["uv", "sync"],
            cwd=str(PROJECT_ROOT),
            check=True,
            capture_output=True,
        )
        console.print("  [green]✓[/green] Python dependencies installed")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        console.print(f"  [yellow]⚠ uv sync failed: {e}[/yellow]")

    # Install gateway deps
    gateway_dir = PROJECT_ROOT / "gateway"
    if gateway_dir.exists():
        console.print("  [cyan]Installing gateway dependencies...[/cyan]")
        try:
            subprocess.run(
                ["bun", "install"],
                cwd=str(gateway_dir),
                check=True,
                capture_output=True,
            )
            console.print("  [green]✓[/green] Gateway dependencies installed")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            console.print(f"  [yellow]⚠ bun install failed: {e}[/yellow]")

    console.print()


def _show_summary(model: str, preset: str, api_keys: dict[str, str]) -> None:
    """Show the final summary."""
    p = PRESETS[preset]

    summary = Table(show_header=False, border_style="cyan", padding=(0, 2))
    summary.add_column("Key", style="dim")
    summary.add_column("Value", style="green")

    summary.add_row("Model", model)
    summary.add_row("Preset", f"{p['emoji']} {preset}")
    summary.add_row("Providers", ", ".join(
        prov["name"] for prov in PROVIDERS if prov["key"] in api_keys
    ))
    summary.add_row("Config", str(CONFIG_FILE.relative_to(PROJECT_ROOT)))
    summary.add_row("Database", "data/windyfly.db")
    summary.add_row("Dashboard", "http://localhost:3000")

    console.print(Panel(summary, title="[bold cyan]🪰 Setup Complete[/bold cyan]", border_style="green"))
    console.print()
    console.print("  [bold]Start your agent:[/bold]")
    console.print("    [green]windy start[/green]          — Brain + Gateway + Dashboard")
    console.print("    [green]windy start --cli[/green]    — Brain + CLI chat (no dashboard)")
    console.print()
    console.print("  [bold]Other commands:[/bold]")
    console.print("    [dim]windy stop[/dim]           — Stop all processes")
    console.print("    [dim]windy status[/dim]         — Check what's running")
    console.print("    [dim]windy init[/dim]           — Re-run this wizard")
    console.print()

    if Confirm.ask("  Launch Windy Fly now?", default=True):
        console.print()
        console.print("  [cyan]Starting Windy Fly...[/cyan]")
        _launch_stack()


def _launch_stack() -> None:
    """Start both brain and gateway by delegating to the unified CLI."""
    import argparse
    from windyfly.cli import cmd_start

    # Build a minimal args namespace that cmd_start expects
    args = argparse.Namespace(cli=False)
    cmd_start(args)


# ═══════════════════════════════════════════════════════════════════════
# Main entry
# ═══════════════════════════════════════════════════════════════════════


def run_wizard() -> None:
    """Run the full setup wizard."""
    from dotenv import load_dotenv

    # Load existing .env so we can detect pre-configured keys
    load_dotenv(ENV_FILE)

    _banner()

    # Check if already configured
    if ENV_FILE.exists() and CONFIG_FILE.exists():
        console.print("[yellow]⚠ Existing configuration found.[/yellow]")
        if not Confirm.ask("  Overwrite and re-configure?", default=False):
            console.print("[dim]  Keeping existing config. Run `windy start` to launch.[/dim]")
            return

    _check_prerequisites()
    api_keys = _step_api_keys()
    model = _step_model(api_keys)
    preset = _step_personality()
    _step_finalize(api_keys, model, preset)
    _show_summary(model, preset, api_keys)


if __name__ == "__main__":
    run_wizard()
