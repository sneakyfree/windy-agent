"""Windy Fly Quickstart — the ``windy go`` zero-friction launcher.

One command. One paste. Talking to your agent in 60 seconds.

    $ windy go

Flow:
    1. Check/install prerequisites (silent if already met)
    2. Ask for ONE API key (or detect from clipboard/environment)
    3. Auto-detect provider from key format
    4. Pick best default model for that provider
    5. Write config with "buddy" personality preset
    6. Install deps (if needed)
    7. Start the stack
    8. Open the dashboard

The user never needs to know what a TOML file is.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from windyfly.platform import IS_WINDOWS, can_run, get_data_dir

console = Console()
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ═══════════════════════════════════════════════════════════════════════
# Key detection — identify provider from key format
# ═══════════════════════════════════════════════════════════════════════

# Order matters: more specific prefixes first
KEY_PATTERNS: list[dict[str, str]] = [
    {
        "prefix": "sk-ant-",
        "env_var": "ANTHROPIC_API_KEY",
        "provider": "Anthropic",
        "model": "claude-3-5-sonnet-latest",
        "url": "https://console.anthropic.com/settings/keys",
    },
    {
        "prefix": "xai-",
        "env_var": "GROK_API_KEY",
        "provider": "xAI Grok",
        "model": "grok-3-mini",
        "url": "https://console.x.ai",
    },
    {
        "prefix": "AIza",
        "env_var": "GEMINI_API_KEY",
        "provider": "Google Gemini",
        "model": "gemini-2.5-flash",
        "url": "https://aistudio.google.com/apikey",
    },
    {
        # OpenAI keys: sk-proj-... or sk-... (but NOT sk-ant-)
        "prefix": "sk-",
        "env_var": "OPENAI_API_KEY",
        "provider": "OpenAI",
        "model": "gpt-4o-mini",
        "url": "https://platform.openai.com/api-keys",
    },
    {
        # DeepSeek also uses sk- but typically longer; we'll catch it
        # if the user explicitly says DeepSeek or via fallback
        "prefix": "dsk-",
        "env_var": "DEEPSEEK_API_KEY",
        "provider": "DeepSeek",
        "model": "deepseek-chat",
        "url": "https://platform.deepseek.com",
    },
]

# Providers the user can choose if we can't auto-detect
PROVIDER_MENU = [
    {"name": "OpenAI", "env_var": "OPENAI_API_KEY", "model": "gpt-4o-mini",
     "url": "https://platform.openai.com/api-keys"},
    {"name": "Anthropic", "env_var": "ANTHROPIC_API_KEY", "model": "claude-3-5-sonnet-latest",
     "url": "https://console.anthropic.com/settings/keys"},
    {"name": "xAI Grok", "env_var": "GROK_API_KEY", "model": "grok-3-mini",
     "url": "https://console.x.ai"},
    {"name": "Google Gemini (free tier)", "env_var": "GEMINI_API_KEY", "model": "gemini-2.5-flash",
     "url": "https://aistudio.google.com/apikey"},
    {"name": "DeepSeek", "env_var": "DEEPSEEK_API_KEY", "model": "deepseek-chat",
     "url": "https://platform.deepseek.com"},
    {"name": "Mistral", "env_var": "MISTRAL_API_KEY", "model": "mistral-large-latest",
     "url": "https://console.mistral.ai/api-keys"},
]


def detect_provider(key: str) -> dict[str, str] | None:
    """Identify the provider from an API key's prefix.

    Returns a dict with provider, env_var, model, url — or None if
    the key format isn't recognized.
    """
    key = key.strip()
    for pattern in KEY_PATTERNS:
        if key.startswith(pattern["prefix"]):
            return pattern
    return None


# ═══════════════════════════════════════════════════════════════════════
# Clipboard helpers
# ═══════════════════════════════════════════════════════════════════════


def read_clipboard() -> str | None:
    """Read the system clipboard. Returns None on failure."""
    try:
        if sys.platform == "darwin":
            result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
            return result.stdout.strip() if result.returncode == 0 else None
        elif IS_WINDOWS:
            result = subprocess.run(
                ["powershell", "-Command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        else:
            # Linux — try xclip, then xsel
            for cmd in [["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]]:
                if shutil.which(cmd[0]):
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                    if result.returncode == 0:
                        return result.stdout.strip()
            return None
    except Exception:
        return None


def watch_clipboard_for_key(timeout_seconds: int = 60) -> tuple[str, dict[str, str]] | None:
    """Watch clipboard for an API key to appear. Returns (key, provider_info) or None."""
    console.print("  [dim]Watching clipboard... copy your API key and we'll grab it.[/dim]")

    initial_clip = read_clipboard() or ""
    start = time.time()

    while time.time() - start < timeout_seconds:
        time.sleep(1)
        current = read_clipboard()
        if current and current != initial_clip and len(current) > 10:
            provider = detect_provider(current)
            if provider:
                return (current, provider)
        # Check for keyboard interrupt
    return None


# ═══════════════════════════════════════════════════════════════════════
# Config writer (reuses setup_wizard's format)
# ═══════════════════════════════════════════════════════════════════════


def write_quick_config(
    env_var: str,
    api_key: str,
    model: str,
    preset: str = "buddy",
) -> None:
    """Write .env and windyfly.toml for quickstart."""
    from windyfly.setup_wizard import PRESETS, PROVIDERS

    preset_data = PRESETS.get(preset, PRESETS["buddy"])
    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Build .env
    env_lines = [
        "# Windy Fly — generated by `windy go`",
        f"DEFAULT_MODEL={model}",
        "",
        "# LLM Providers",
    ]
    for provider in PROVIDERS:
        if provider["key"] == env_var:
            env_lines.append(f"{provider['key']}={api_key}")
        else:
            env_lines.append(f"{provider['key']}=")

    env_lines.extend([
        "",
        "# Database",
        "WINDYFLY_DB_PATH=data/windyfly.db",
        "",
        "# Logging",
        "LOG_LEVEL=INFO",
        "",
        "# Matrix / Windy Chat (optional)",
        "MATRIX_HOMESERVER=https://chat.windypro.com",
        "MATRIX_BOT_USER=@windyfly:chat.windypro.com",
        "MATRIX_BOT_TOKEN=",
        "MATRIX_BOT_PASSWORD=",
        "",
        "# Windy Pro API (optional)",
        "WINDY_API_URL=http://localhost:8098",
        "WINDY_JWT=",
    ])

    (PROJECT_ROOT / ".env").write_text("\n".join(env_lines) + "\n")

    # Build windyfly.toml
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
homeserver = "https://chat.windypro.com"
bot_user = "@windyfly:chat.windypro.com"

[windy_api]
base_url = "http://localhost:8098"
"""
    (PROJECT_ROOT / "windyfly.toml").write_text(toml_content)


# ═══════════════════════════════════════════════════════════════════════
# The main event: windy go
# ═══════════════════════════════════════════════════════════════════════


def cmd_go(args: Any) -> None:
    """The zero-friction quickstart. One command, one paste, done."""

    # ── Non-interactive fast path: --key flag ────────────────────
    key_arg = getattr(args, "key", None)
    if key_arg:
        _go_noninteractive(args)
        return

    console.print()
    console.print(
        Panel(
            "[bold]🪰 Windy Fly[/bold] — Let's get you set up in 60 seconds.",
            border_style="cyan",
            padding=(1, 4),
        )
    )
    console.print()

    # ── Step 1: Prerequisites (silent if met) ────────────────────
    missing = []
    if not can_run("uv"):
        missing.append("uv")
    if not can_run("bun"):
        missing.append("bun")

    if missing:
        console.print(f"  [yellow]Installing missing tools: {', '.join(missing)}...[/yellow]")
        _install_prereqs(missing)
        console.print()

    # ── Step 2: Check for existing config ────────────────────────
    env_file = PROJECT_ROOT / ".env"
    toml_file = PROJECT_ROOT / "windyfly.toml"
    if env_file.exists() and toml_file.exists():
        # Already configured — just start
        console.print("  [green]✓[/green] Configuration found")
        # Check if it has a real API key
        env_content = env_file.read_text()
        has_key = any(
            line.split("=", 1)[1].strip()
            for line in env_content.splitlines()
            if "_API_KEY=" in line and len(line.split("=", 1)[1].strip()) > 8
        )
        if has_key:
            console.print("  [green]✓[/green] API key configured")
            console.print()
            if Confirm.ask("  Already set up! Launch Windy Fly?", default=True):
                _launch(args)
            return

    # ── Step 3: Check environment for existing keys ──────────────
    for pattern in KEY_PATTERNS:
        env_val = os.environ.get(pattern["env_var"], "")
        if env_val and len(env_val) > 10:
            provider = pattern
            console.print(f"  [green]✓[/green] Found {provider['provider']} key in environment")
            console.print(f"  [green]✓[/green] Default model: [bold]{provider['model']}[/bold]")
            console.print()
            write_quick_config(provider["env_var"], env_val, provider["model"])
            console.print("  [green]✓[/green] Configuration written")
            _install_deps()
            _launch(args)
            return

    # ── Step 4: Check clipboard for a key ────────────────────────
    clip = read_clipboard()
    if clip and len(clip) > 10:
        provider = detect_provider(clip)
        if provider:
            console.print(f"  [cyan]Found a {provider['provider']} API key on your clipboard![/cyan]")
            if Confirm.ask(f"  Use this key for {provider['provider']}?", default=True):
                write_quick_config(provider["env_var"], clip, provider["model"])
                console.print(f"  [green]✓[/green] Configured with {provider['provider']} ({provider['model']})")
                _install_deps()
                _launch(args)
                return
            console.print()

    # ── Step 5: Ask for a key ────────────────────────────────────
    console.print("  [bold]Pick your AI provider:[/bold]")
    console.print()
    for i, p in enumerate(PROVIDER_MENU, 1):
        console.print(f"    [bold]{i}[/bold]  {p['name']}")
    console.print()
    console.print(f"    [bold]0[/bold]  I don't have a key yet — help me get one")
    console.print()

    choice = Prompt.ask("  Choice", default="1")

    try:
        idx = int(choice)
    except ValueError:
        idx = 1

    if idx == 0:
        # Guided signup flow — walks them through creating an account + key
        result = _help_get_key()
        if result is None:
            return
        api_key, provider_info = result
        # Validate, write config, and launch
        console.print()
        console.print(f"  [cyan]Validating {provider_info['provider']} key...[/cyan]")
        valid = _validate_key(provider_info["env_var"], api_key)
        if valid:
            console.print(f"  [green]✓[/green] Key is valid!")
        else:
            console.print(f"  [yellow]⚠ Couldn't verify key (saving anyway — it may still work)[/yellow]")
        console.print()
        write_quick_config(provider_info["env_var"], api_key, provider_info["model"])
        console.print(f"  [green]✓[/green] Config written — {provider_info['provider']} / {provider_info['model']} / 🤝 buddy preset")
        _try_matrix_provision()
        _try_mail_provision()
        _install_deps()
        _launch(args)
        return

    if idx < 1 or idx > len(PROVIDER_MENU):
        idx = 1

    selected = PROVIDER_MENU[idx - 1]
    console.print()
    console.print(f"  [bold]{selected['name']}[/bold] — paste your API key below.")
    console.print(f"  [dim]Don't have one? Get it at: {selected['url']}[/dim]")
    console.print()

    # Offer to open the browser
    if Confirm.ask("  Open the API key page in your browser?", default=True):
        try:
            webbrowser.open(selected["url"])
        except Exception:
            pass
        console.print()
        console.print("  [dim]Copy the key from your browser, then paste it here.[/dim]")

        # Try clipboard watching first
        console.print()
        result = _try_clipboard_or_paste(selected)
    else:
        console.print()
        result = _prompt_for_key(selected)

    if result is None:
        console.print("  [red]No key provided. Run [bold]windy go[/bold] again when you're ready.[/red]")
        return

    api_key, provider_info = result

    # Validate the key
    console.print()
    console.print(f"  [cyan]Validating {provider_info['provider']} key...[/cyan]")
    valid = _validate_key(provider_info["env_var"], api_key)
    if valid:
        console.print(f"  [green]✓[/green] Key is valid!")
    else:
        console.print(f"  [yellow]⚠ Couldn't verify key (saving anyway — it may still work)[/yellow]")

    # Write config
    console.print()
    write_quick_config(provider_info["env_var"], api_key, provider_info["model"])
    console.print(f"  [green]✓[/green] Config written — {provider_info['provider']} / {provider_info['model']} / 🤝 buddy preset")

    # Auto-provision Matrix bot (Windy Chat)
    _try_matrix_provision()

    # Auto-provision Windy Mail inbox
    _try_mail_provision()

    # Install deps and launch
    _install_deps()
    _launch(args)


# ═══════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════


def _go_noninteractive(args: Any) -> None:
    """Non-interactive quickstart: --key provided, zero prompts.

    Usage::

        windy go --key sk-ant-abc123
        windy go --key sk-abc123 --model gpt-4o --preset engineer
        windy go --key sk-abc123 --no-browser
        OPENAI_API_KEY=sk-abc123 windy go --key $OPENAI_API_KEY
    """
    key = args.key.strip()
    model_override = getattr(args, "model", None)
    preset = getattr(args, "preset", None) or "buddy"
    no_browser = getattr(args, "no_browser", False)

    console.print()
    console.print("[bold cyan]🪰 Windy Fly — non-interactive setup[/bold cyan]")
    console.print()

    # Prerequisites
    missing = []
    if not can_run("uv"):
        missing.append("uv")
    if not can_run("bun"):
        missing.append("bun")
    if missing:
        console.print(f"  [cyan]Installing: {', '.join(missing)}...[/cyan]")
        _install_prereqs(missing)

    # Detect provider from key
    provider = detect_provider(key)
    if provider:
        env_var = provider["env_var"]
        provider_name = provider["provider"]
        model = model_override or provider["model"]
        console.print(f"  [green]✓[/green] Detected provider: [bold]{provider_name}[/bold]")
    else:
        # Unrecognized key format — try to use it as OpenAI (most common)
        console.print(f"  [yellow]⚠ Unrecognized key format — assuming OpenAI[/yellow]")
        env_var = "OPENAI_API_KEY"
        provider_name = "OpenAI"
        model = model_override or "gpt-4o-mini"

    console.print(f"  [green]✓[/green] Model: [bold]{model}[/bold]")
    console.print(f"  [green]✓[/green] Preset: [bold]{preset}[/bold]")

    # Validate key
    console.print(f"  [cyan]Validating key...[/cyan]")
    valid = _validate_key(env_var, key)
    if valid:
        console.print(f"  [green]✓[/green] Key valid")
    else:
        console.print(f"  [yellow]⚠ Could not verify (saving anyway)[/yellow]")

    # Write config
    write_quick_config(env_var, key, model, preset)
    console.print(f"  [green]✓[/green] Configuration written")

    # Auto-provision Matrix bot (Windy Chat)
    _try_matrix_provision()

    # Auto-provision Windy Mail inbox
    _try_mail_provision()

    # Install deps
    _install_deps()

    # Launch
    _launch(args)


def _try_matrix_provision() -> None:
    """Attempt to auto-provision Matrix bot credentials."""
    try:
        from windyfly.matrix_provision import auto_provision_and_save
        auto_provision_and_save()
    except Exception:
        # Matrix provisioning is a nice-to-have, never a blocker
        console.print("  [dim]○ Windy Chat — skipped[/dim]")


def _try_mail_provision() -> None:
    """Attempt to auto-provision a Windy Mail inbox."""
    try:
        import asyncio
        from windyfly.mail_provision import provision_mail

        agent_name = os.environ.get("WINDYFLY_AGENT_NAME", "windyfly")
        eternitas_passport = os.environ.get("ETERNITAS_PASSPORT", "")
        owner_id = os.environ.get("WINDY_OWNER_ID", "")

        if not eternitas_passport:
            console.print("  [dim]○ Windy Mail — skipped (no Eternitas passport)[/dim]")
            return

        console.print("  [cyan]Provisioning Windy Mail inbox...[/cyan]")
        result = asyncio.run(provision_mail(agent_name, eternitas_passport, owner_id))
        if result:
            console.print(f"  [green]✓[/green] Windy Mail — {result['email']} provisioned")
        else:
            console.print("  [dim]○ Windy Mail — skipped[/dim]")
    except Exception:
        # Mail provisioning is a nice-to-have, never a blocker
        console.print("  [dim]○ Windy Mail — skipped[/dim]")


def _try_clipboard_or_paste(provider: dict) -> tuple[str, dict[str, str]] | None:
    """Try clipboard watching for a few seconds, fall back to manual paste."""
    console.print("  [dim]Watching clipboard for 15 seconds... or just paste below.[/dim]")
    console.print()

    # Quick clipboard poll — non-blocking feel
    initial_clip = read_clipboard() or ""
    for _ in range(15):
        time.sleep(1)
        current = read_clipboard()
        if current and current != initial_clip and len(current) > 10:
            detected = detect_provider(current)
            if detected:
                console.print(f"  [green]✓ Detected {detected['provider']} key from clipboard![/green]")
                return (current, detected)

        # Check if any input is waiting (can't truly do this in Python
        # without threads, so we fall through after the timeout)

    console.print("  [dim]No key detected on clipboard — paste it manually:[/dim]")
    return _prompt_for_key(provider)


def _prompt_for_key(provider: dict) -> tuple[str, dict[str, str]] | None:
    """Prompt user to paste their API key."""
    key = Prompt.ask(
        "  Paste API key",
        default="",
        show_default=False,
    )
    key = key.strip()
    if not key:
        return None

    # Try to auto-detect provider from what they pasted
    detected = detect_provider(key)
    if detected:
        return (key, detected)

    # Couldn't auto-detect — use the provider they selected
    return (key, {
        "env_var": provider["env_var"],
        "provider": provider["name"],
        "model": provider["model"],
        "url": provider["url"],
    })


# ═══════════════════════════════════════════════════════════════════════
# Guided signup walkthroughs — the "hotel ballroom" experience
# ═══════════════════════════════════════════════════════════════════════

SIGNUP_GUIDES: list[dict[str, Any]] = [
    {
        "name": "Google Gemini",
        "tag": "FREE — no credit card needed",
        "tag_style": "bold green",
        "env_var": "GEMINI_API_KEY",
        "model": "gemini-2.5-flash",
        "url": "https://aistudio.google.com/apikey",
        "steps": [
            "We'll open [bold]Google AI Studio[/bold] in your browser",
            "Sign in with your [bold]Google account[/bold] (Gmail, YouTube, etc.)",
            "Click the blue [bold]\"Create API Key\"[/bold] button",
            "Click [bold]\"Copy\"[/bold] next to the key — we'll detect it automatically",
        ],
    },
    {
        "name": "xAI Grok",
        "tag": "$25 FREE credits with X account",
        "tag_style": "bold cyan",
        "env_var": "GROK_API_KEY",
        "model": "grok-3-mini",
        "url": "https://console.x.ai",
        "steps": [
            "We'll open the [bold]xAI Console[/bold] in your browser",
            "Sign in with your [bold]X (Twitter) account[/bold]",
            "Go to [bold]API Keys[/bold] in the left sidebar",
            "Click [bold]\"Create API Key\"[/bold] and copy it",
        ],
    },
    {
        "name": "DeepSeek",
        "tag": "Free credits — email signup only",
        "tag_style": "bold cyan",
        "env_var": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
        "url": "https://platform.deepseek.com/api_keys",
        "steps": [
            "We'll open the [bold]DeepSeek Platform[/bold] in your browser",
            "Click [bold]\"Sign Up\"[/bold] — just needs an email address",
            "Verify your email, then go to [bold]API Keys[/bold]",
            "Click [bold]\"Create New API Key\"[/bold] and copy it",
        ],
    },
    {
        "name": "OpenAI",
        "tag": "Most popular — powers ChatGPT",
        "tag_style": "bold",
        "env_var": "OPENAI_API_KEY",
        "model": "gpt-4o-mini",
        "url": "https://platform.openai.com/api-keys",
        "steps": [
            "We'll open the [bold]OpenAI Platform[/bold] in your browser",
            "Click [bold]\"Sign Up\"[/bold] (or \"Log In\" if you have a ChatGPT account)",
            "Add a payment method [dim](pay-as-you-go, a few cents per chat)[/dim]",
            "Click [bold]\"Create new secret key\"[/bold] and copy it",
        ],
    },
    {
        "name": "Anthropic",
        "tag": "Best for coding & reasoning — powers Claude",
        "tag_style": "bold",
        "env_var": "ANTHROPIC_API_KEY",
        "model": "claude-3-5-sonnet-latest",
        "url": "https://console.anthropic.com/settings/keys",
        "steps": [
            "We'll open the [bold]Anthropic Console[/bold] in your browser",
            "Click [bold]\"Sign Up\"[/bold] to create an account",
            "Add a payment method [dim](pay-as-you-go)[/dim]",
            "Go to [bold]API Keys[/bold] → [bold]\"Create Key\"[/bold] and copy it",
        ],
    },
    {
        "name": "Mistral",
        "tag": "European AI — small free tier",
        "tag_style": "dim",
        "env_var": "MISTRAL_API_KEY",
        "model": "mistral-large-latest",
        "url": "https://console.mistral.ai/api-keys",
        "steps": [
            "We'll open the [bold]Mistral Console[/bold] in your browser",
            "Click [bold]\"Sign Up\"[/bold] with your email",
            "Go to [bold]API Keys[/bold] in the dashboard",
            "Click [bold]\"Create New Key\"[/bold] and copy it",
        ],
    },
]


def _help_get_key() -> tuple[str, dict[str, str]] | None:
    """Guided provider signup — walks a total beginner through getting their first API key.

    Returns (api_key, provider_info) or None if they bail out.
    """
    console.print()
    console.print(
        Panel(
            "[bold]No worries! Let's get you set up with an AI provider.[/bold]\n"
            "[dim]This takes about 2 minutes. We'll walk you through every step.[/dim]",
            border_style="cyan",
        )
    )
    console.print()

    # Show provider options
    console.print("  [bold]Choose a provider to sign up with:[/bold]")
    console.print()

    for i, guide in enumerate(SIGNUP_GUIDES, 1):
        tag = f"[{guide['tag_style']}]{guide['tag']}[/{guide['tag_style']}]"
        if i == 1:
            console.print(f"    [bold green]→ {i}[/bold green]  [bold]{guide['name']}[/bold]  {tag}")
            console.print(f"         [green]Recommended for first-time users[/green]")
        else:
            console.print(f"      {i}   {guide['name']}  {tag}")
        console.print()

    choice = Prompt.ask("  Which provider?", default="1")

    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(SIGNUP_GUIDES):
            idx = 0
    except ValueError:
        idx = 0

    guide = SIGNUP_GUIDES[idx]

    # Show step-by-step walkthrough
    console.print()
    console.print(
        Panel(
            f"[bold]{guide['name']} Setup[/bold]\n"
            + "\n".join(f"  [bold]Step {i}:[/bold] {step}" for i, step in enumerate(guide["steps"], 1)),
            border_style="cyan",
            title=f"[dim]{guide['tag']}[/dim]",
        )
    )
    console.print()

    # Open the browser
    console.print("  Press [bold]Enter[/bold] to open the signup page in your browser...")
    Prompt.ask("  ", default="", show_default=False)

    try:
        webbrowser.open(guide["url"])
    except Exception:
        console.print(f"  [dim]Could not open browser. Go to: {guide['url']}[/dim]")

    console.print()
    console.print(f"  [green]✓[/green] Browser opened to [bold]{guide['name']}[/bold]")
    console.print()
    console.print("  [bold]Follow the steps above in your browser.[/bold]")
    console.print("  When you see your API key, [bold]copy it[/bold] (Ctrl+C / Cmd+C).")
    console.print()

    # Clipboard watcher + manual paste fallback
    console.print("  [cyan]Watching for your key...[/cyan]")
    console.print("  [dim]We'll auto-detect it from your clipboard, or paste it below.[/dim]")
    console.print()

    # Watch clipboard in a loop, but also accept manual paste
    provider_info = {
        "env_var": guide["env_var"],
        "provider": guide["name"],
        "model": guide["model"],
        "url": guide["url"],
    }

    result = _clipboard_watch_with_paste_fallback(provider_info, timeout=120)
    return result


def _clipboard_watch_with_paste_fallback(
    provider: dict,
    timeout: int = 120,
) -> tuple[str, dict[str, str]] | None:
    """Watch clipboard while offering manual paste. Longer timeout for signups."""
    import threading

    initial_clip = read_clipboard() or ""
    found_key: list[tuple[str, dict[str, str]]] = []

    def _watch() -> None:
        """Poll clipboard in background thread, looking for an API key."""
        nonlocal found_key
        start = time.time()
        while time.time() - start < timeout and not found_key:
            time.sleep(1.5)
            current = read_clipboard()
            if current and current != initial_clip and len(current) > 10:
                detected = detect_provider(current)
                if detected:
                    found_key.append((current, detected))
                    return

    # Start clipboard watcher in background
    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()

    # Meanwhile, prompt for manual paste (non-blocking feel)
    console.print("  [dim]Paste your key here when ready (or wait for auto-detect):[/dim]")
    key = Prompt.ask("  API key", default="", show_default=False)
    key = key.strip()

    if key:
        # Manual paste — detect provider
        detected = detect_provider(key)
        if detected:
            return (key, detected)
        return (key, provider)

    # Check if clipboard watcher found something
    if found_key:
        k, p = found_key[0]
        console.print(f"  [green]✓ Detected {p['provider']} key from clipboard![/green]")
        return (k, p)

    # Wait a bit more for the watcher
    console.print("  [dim]Still watching clipboard...[/dim]")
    watcher.join(timeout=30)
    if found_key:
        k, p = found_key[0]
        console.print(f"  [green]✓ Detected {p['provider']} key from clipboard![/green]")
        return (k, p)

    console.print("  [yellow]No key detected.[/yellow]")
    console.print("  [dim]Take your time — run [bold]windy go[/bold] again when you have it.[/dim]")
    return None


def _validate_key(env_var: str, key: str) -> bool:
    """Quick validation of an API key."""
    try:
        import httpx

        if env_var == "OPENAI_API_KEY":
            r = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            return r.status_code == 200

        if env_var == "ANTHROPIC_API_KEY":
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
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
            return r.status_code in (200, 400)

        if env_var == "GROK_API_KEY":
            r = httpx.get(
                "https://api.x.ai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            return r.status_code == 200

        # For others, just check length
        return len(key) > 10

    except Exception:
        return False


def _install_prereqs(missing: list[str]) -> None:
    """Install missing prerequisites."""
    if "uv" in missing:
        console.print("  [cyan]Installing uv...[/cyan]")
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["powershell", "-ExecutionPolicy", "ByPass", "-c",
                     "irm https://astral.sh/uv/install.ps1 | iex"],
                    check=True, capture_output=True,
                )
            else:
                subprocess.run(
                    ["bash", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
                    check=True, capture_output=True,
                )
            console.print("  [green]✓[/green] uv installed")
        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print("  [red]✗ Could not install uv. Visit: https://docs.astral.sh/uv/[/red]")
            sys.exit(1)

    if "bun" in missing:
        console.print("  [cyan]Installing Bun...[/cyan]")
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["powershell", "-ExecutionPolicy", "ByPass", "-c",
                     "irm https://bun.sh/install.ps1 | iex"],
                    check=True, capture_output=True,
                )
            else:
                subprocess.run(
                    ["bash", "-c", "curl -fsSL https://bun.sh/install | bash"],
                    check=True, capture_output=True,
                )
            console.print("  [green]✓[/green] Bun installed")
        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print("  [red]✗ Could not install Bun. Visit: https://bun.sh[/red]")
            sys.exit(1)


def _install_deps() -> None:
    """Ensure Python and gateway dependencies are installed."""
    console.print()
    console.print("  [cyan]Installing dependencies...[/cyan]")

    # Python deps
    result = subprocess.run(
        ["uv", "sync"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True,
    )
    if result.returncode == 0:
        console.print("  [green]✓[/green] Python deps")
    else:
        console.print(f"  [yellow]⚠ uv sync had issues[/yellow]")

    # Gateway deps
    gateway_dir = PROJECT_ROOT / "gateway"
    if gateway_dir.exists():
        result = subprocess.run(
            ["bun", "install"],
            cwd=str(gateway_dir), capture_output=True, text=True,
        )
        if result.returncode == 0:
            console.print("  [green]✓[/green] Gateway deps")
        else:
            console.print(f"  [yellow]⚠ bun install had issues[/yellow]")


def _launch(args: Any) -> None:
    """Start Windy Fly and open the dashboard."""
    import argparse
    from windyfly.cli import cmd_start

    no_browser = getattr(args, "no_browser", False)

    console.print()
    # Build args namespace cmd_start expects
    start_args = argparse.Namespace(cli=False, no_browser=no_browser)
    cmd_start(start_args)
