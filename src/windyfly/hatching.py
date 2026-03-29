"""The Hatching Ceremony — Windy Fly's signature "IT'S ALIVE!" moment.

Plays every time the agent successfully launches.  The brand moment
that people will screenshot, screen-record, and share.

    Terminal:  Rich animated ASCII art + lightning + mad scientist
    Dashboard: Animated hatching sequence (served via gateway)
    Audio:     Hook for future ``afplay`` / browser Audio() integration
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ═══════════════════════════════════════════════════════════════════════
# The ASCII Art
# ═══════════════════════════════════════════════════════════════════════

LIGHTNING = """
[bold yellow]          ⚡        ⚡
           \\  ⚡⚡  /
            ⚡    ⚡[/bold yellow]
"""

FLY_HATCH = """[bold cyan]
       ╔══════════════════╗
       ║   [bold white]🧬  🪰  🧬[/bold white]   ║
       ╚══════════════════╝
[/bold cyan]"""

MAD_SCIENTIST = """[dim]
            .___.
           /     \\
          | () () |
           \\  ^  /      [bold green]"IT'S ALIVE!!!"[/bold green]
            |||||
           /|   |\\      [bold green]"IT'S ALIVE!!!"[/bold green]
     ~~~  / |   | \\
         /  |   |  \\    [bold green]"THE FLY IS ALIVE!!!"[/bold green]
   ~~~~ /  /|   |\\  \\
       /__/ |___| \\__\\
           /     \\
          /       \\
         ~~~     ~~~
[/dim]"""

ITS_ALIVE_BANNER = """
[bold green]  ╦╔╦╗╔═╗  ╔═╗╦  ╦╦  ╦╔═╗╦
  ║ ║ ╚═╗  ╠═╣║  ║╚╗╔╝║╣ ║
  ╩ ╩ ╚═╝  ╩ ╩╩═╝╩ ╚╝ ╚═╝o[/bold green]

[bold cyan]  🪰  The Fly Is Alive  🪰[/bold cyan]
"""

# Compact version for narrow terminals
ITS_ALIVE_COMPACT = """
[bold green]  ╔══════════════════════════════════════════╗
  ║  IT'S ALIVE! IT'S ALIVE!                ║
  ║  THE FLY IS ALIVE!  🪰                  ║
  ╚══════════════════════════════════════════╝[/bold green]
"""


def play_hatching(animate: bool = True) -> None:
    """Play the full hatching ceremony in the terminal.

    THIS ALWAYS PLAYS.  Every HiFly descendant — HiFly, Windy Fly, or
    any future fork — gets the full "IT'S ALIVE!" ceremony.  This is
    the signature of the framework.  Hardcoded.  Non-negotiable.

    Args:
        animate: If True, adds dramatic pauses between stages.
                 Set False for non-interactive / CI environments.
    """
    width = console.width or 80

    # Use compact version for narrow terminals
    use_compact = width < 75

    if animate:
        # Stage 1: Lightning
        console.print(LIGHTNING)
        time.sleep(0.4)

        # Stage 2: The fly emerges
        console.print(FLY_HATCH)
        time.sleep(0.3)

        # Stage 3: Mad scientist
        console.print(MAD_SCIENTIST)
        time.sleep(0.3)

        # Stage 4: IT'S ALIVE!!!
        if use_compact:
            console.print(ITS_ALIVE_COMPACT)
        else:
            console.print(ITS_ALIVE_BANNER)
        time.sleep(0.5)
    else:
        # Non-animated: just show the result
        console.print()
        if use_compact:
            console.print(ITS_ALIVE_COMPACT)
        else:
            console.print(ITS_ALIVE_BANNER)
        console.print(MAD_SCIENTIST)

    # Audio hook — play sound if available
    _try_play_audio()


def _try_play_audio() -> None:
    """Try to play the hatching sound effect.

    Looks for ``data/sounds/its-alive.mp3`` or ``.wav``.
    Falls back silently if no audio file or no player available.
    """
    sounds_dir = PROJECT_ROOT / "data" / "sounds"
    for ext in ("mp3", "wav", "ogg"):
        sound_file = sounds_dir / f"its-alive.{ext}"
        if sound_file.exists():
            try:
                import subprocess
                import sys
                if sys.platform == "darwin":
                    subprocess.Popen(
                        ["afplay", str(sound_file)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                elif sys.platform == "win32":
                    # Windows Media Player CLI
                    subprocess.Popen(
                        ["powershell", "-Command",
                         f"(New-Object Media.SoundPlayer '{sound_file}').PlaySync()"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    # Linux: try aplay, paplay, or mpv
                    for player in ["paplay", "aplay", "mpv --no-video"]:
                        cmd = player.split()
                        import shutil
                        if shutil.which(cmd[0]):
                            subprocess.Popen(
                                cmd + [str(sound_file)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            break
            except Exception:
                pass  # Audio is a nice-to-have, never a blocker
            return


# ═══════════════════════════════════════════════════════════════════════
# "Born Into" — Ecosystem Status Display
# ═══════════════════════════════════════════════════════════════════════

def show_ecosystem_status(hatch_result=None) -> None:
    """Show what the agent was born with — the ecosystem power moment.

    For Windy Fly: full ecosystem manifest (Chat, SMS, Email, Translation, etc.)
    For HiFly: simplified status (model, dashboard, memory).

    Args:
        hatch_result: Optional HatchResult from the hatch orchestrator.
            If provided, uses real provisioned data instead of env var checks.
    """
    from dotenv import load_dotenv
    from windyfly.branding import HAS_ECOSYSTEM, BRAND_NAME, BRAND_EMOJI
    load_dotenv(PROJECT_ROOT / ".env")

    capabilities: list[tuple[str, str, bool]] = []

    # ── Eternitas (identity) ──
    passport_id = getattr(hatch_result, "passport_id", "") or os.environ.get("ETERNITAS_PASSPORT", "")
    if passport_id:
        capabilities.append(("🪪", f"Eternitas — verified ({passport_id})", True))
    else:
        capabilities.append(("🪪", "Eternitas — not registered", False))

    # ── Windy Chat (Matrix) ──
    matrix_user = getattr(hatch_result, "matrix_user_id", "") if hatch_result else ""
    if not matrix_user:
        matrix_token = os.environ.get("MATRIX_BOT_TOKEN", "")
        matrix_password = os.environ.get("MATRIX_BOT_PASSWORD", "")
        matrix_ready = bool(matrix_token) or bool(matrix_password)
        homeserver = os.environ.get("MATRIX_HOMESERVER", "chat.windypro.com")
        if matrix_ready:
            capabilities.append(("💬", f"Windy Chat — connected to {homeserver}", True))
        else:
            capabilities.append(("💬", "Windy Chat — ready to connect", False))
    else:
        capabilities.append(("💬", f"Windy Chat — {matrix_user}", True))

    # ── Email (Windy Mail) ──
    email_addr = getattr(hatch_result, "email_address", "") if hatch_result else ""
    if not email_addr:
        email_addr = os.environ.get("WINDYMAIL_EMAIL", "") or os.environ.get("WINDYFLY_EMAIL_ADDRESS", "")
    if email_addr:
        capabilities.append(("📧", f"Windy Mail — {email_addr}", True))
    elif os.environ.get("SENDGRID_API_KEY"):
        capabilities.append(("📧", "Email — SendGrid configured", True))
    else:
        capabilities.append(("📧", "Windy Mail — pending", False))

    # ── Phone / SMS ──
    phone = getattr(hatch_result, "phone_number", "") if hatch_result else ""
    phone_mock = getattr(hatch_result, "phone_is_mock", False) if hatch_result else False
    if not phone:
        phone = os.environ.get("TWILIO_PHONE_NUMBER", "")
    if phone:
        tag = " (local)" if phone_mock else ""
        capabilities.append(("📱", f"Phone — {phone}{tag}", True))
    else:
        capabilities.append(("📱", "Phone — add Twilio creds to enable", False))

    # ── LLM Provider ──
    model = getattr(hatch_result, "model_id", "") if hatch_result else ""
    if not model:
        model = os.environ.get("DEFAULT_MODEL", "")
    if model:
        capabilities.append(("🧠", f"AI Brain — {model}", True))

    # ── Translation (Windy Pro API) ──
    windy_jwt = os.environ.get("WINDY_JWT", "")
    windy_api = os.environ.get("WINDY_API_URL", "")
    if windy_jwt and windy_api:
        capabilities.append(("🌍", "199 languages — Windy Traveler connected", True))
    else:
        capabilities.append(("🌍", "Translation — connect Windy Pro to enable", False))

    # ── Trust Dashboard ──
    capabilities.append(("🎛️", "Trust Dashboard — http://localhost:3000", True))

    # ── Memory ──
    capabilities.append(("🧬", "Memory — SQLite + vector search active", True))

    # ── Birth Certificate ──
    cert_num = getattr(hatch_result, "certificate_number", "") if hatch_result else ""
    cert_path = getattr(hatch_result, "birth_certificate_path", "") if hatch_result else ""
    if cert_num:
        capabilities.append(("📜", f"Birth Certificate — {cert_num}", True))

    # ── Build the display ──
    console.print()
    lines = []
    for icon, desc, active in capabilities:
        if active:
            lines.append(f"  [green]✓[/green] {icon}  {desc}")
        else:
            lines.append(f"  [dim]○[/dim] {icon}  [dim]{desc}[/dim]")

    console.print(Panel(
        "\n".join(lines),
        title=f"[bold cyan]{BRAND_EMOJI} {'Born Into the Windy Ecosystem' if HAS_ECOSYSTEM else f'{BRAND_NAME} Status'}[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))

    # Show birth certificate path if generated
    if cert_path:
        console.print(f"  [dim]📜 Birth certificate saved: {cert_path}[/dim]")

    console.print()
