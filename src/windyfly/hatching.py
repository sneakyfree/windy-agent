"""The Hatching Ceremony — Windy Fly's signature "IT'S ALIVE!" moment.

Plays every time the agent successfully launches.  The brand moment
that people will screenshot, screen-record, and share.

    Terminal:  Rich animated ASCII art + lightning + mad scientist
    Dashboard: Animated hatching sequence (served via gateway)
    Audio:     Hook for future ``afplay`` / browser Audio() integration
"""

from __future__ import annotations

import logging
import os
import time

from rich.console import Console

from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)
console = Console()
PROJECT_ROOT = get_project_root()


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
            except Exception as e:
                logger.debug("Audio playback failed: %s", e)
            return


# ═══════════════════════════════════════════════════════════════════════
# "Born Into" — Ecosystem Status Display
# ═══════════════════════════════════════════════════════════════════════

def show_ecosystem_status(hatch_result=None, config: dict | None = None) -> None:
    """Show what the agent was born with — the ecosystem power moment.

    Displays a rich table of all ecosystem products and their provisioning
    status. Uses real data from HatchResult if available, otherwise checks
    environment variables. Shows clear mock vs real mode indicators.

    Args:
        hatch_result: Optional HatchResult from the hatch orchestrator.
        config: Optional config dict with ecosystem URLs.
    """
    from dotenv import load_dotenv
    from rich.table import Table

    from windyfly.branding import BRAND_EMOJI

    load_dotenv(PROJECT_ROOT / ".env")

    eco = config.get("ecosystem", {}) if config else {}

    # Detect if any ecosystem URLs are configured
    has_real_services = any(eco.get(k) for k in (
        "eternitas_url", "windy_mail_url", "matrix_homeserver",
        "windy_cloud_url", "windy_pro_url",
    ))

    table = Table(
        title=f"{BRAND_EMOJI} Windy Fly \u2014 Born Into the Ecosystem",
        title_style="bold cyan",
        border_style="cyan",
        show_lines=True,
        padding=(0, 1),
    )
    table.add_column("Product", style="bold", min_width=12)
    table.add_column("Status", min_width=9)
    table.add_column("Details", min_width=30)

    errors = getattr(hatch_result, "errors", []) if hatch_result else []

    # ── Eternitas ──
    passport_id = getattr(hatch_result, "passport_id", "") or os.environ.get("ETERNITAS_PASSPORT", "")
    eternitas_errors = [e for e in errors if e.startswith("Eternitas:")]
    if passport_id:
        mode = "" if eco.get("eternitas_url") else " (local)"
        table.add_row("Eternitas", "[green]Active[/green]", f"{passport_id}{mode}")
    elif eternitas_errors:
        table.add_row("Eternitas", "[yellow]Offline[/yellow]", "\u26a0\ufe0f  Agent identity: offline (will retry)")
    else:
        table.add_row("Eternitas", "[dim]Pending[/dim]", "Not registered")

    # ── Windy Chat ──
    matrix_user = getattr(hatch_result, "matrix_user_id", "") if hatch_result else ""
    matrix_errors = [e for e in errors if e.startswith("Matrix:")]
    if not matrix_user:
        matrix_ready = bool(os.environ.get("MATRIX_BOT_TOKEN") or os.environ.get("MATRIX_BOT_PASSWORD"))
        homeserver = eco.get("matrix_homeserver") or os.environ.get("MATRIX_HOMESERVER", "chat.windypro.com")
        if matrix_ready:
            table.add_row("Windy Chat", "[green]Active[/green]", f"@windyfly:{homeserver.replace('https://', '')}")
        elif matrix_errors:
            table.add_row("Windy Chat", "[yellow]Offline[/yellow]", "\u26a0\ufe0f  Chat: offline (will connect when available)")
        else:
            table.add_row("Windy Chat", "[dim]Pending[/dim]", "Set SYNAPSE_REGISTRATION_SECRET to enable")
    else:
        table.add_row("Windy Chat", "[green]Active[/green]", matrix_user)

    # ── Windy Mail ──
    email_addr = getattr(hatch_result, "email_address", "") if hatch_result else ""
    mail_errors = [e for e in errors if e.startswith("Mail:")]
    if not email_addr:
        email_addr = os.environ.get("WINDYMAIL_EMAIL", "")
    if email_addr:
        mode = "" if eco.get("windy_mail_url") else " (local)"
        table.add_row("Windy Mail", "[green]Active[/green]", f"{email_addr}{mode}")
    elif mail_errors:
        table.add_row("Windy Mail", "[yellow]Offline[/yellow]", "\u26a0\ufe0f  Email: offline (will be available when mail service is up)")
    else:
        table.add_row("Windy Mail", "[dim]Pending[/dim]", "Set ecosystem.windy_mail_url in windyfly.toml")

    # ── Phone ──
    phone = getattr(hatch_result, "phone_number", "") if hatch_result else ""
    phone_mock = getattr(hatch_result, "phone_is_mock", False) if hatch_result else False
    if not phone:
        phone = os.environ.get("TWILIO_PHONE_NUMBER", "")
    if phone:
        tag = " (local)" if phone_mock else ""
        status = "[yellow]Local[/yellow]" if phone_mock else "[green]Active[/green]"
        table.add_row("Phone", status, f"{phone}{tag}")
    else:
        table.add_row("Phone", "[dim]Pending[/dim]", "Add Twilio creds to enable")

    # ── Birth Certificate ──
    cert_num = getattr(hatch_result, "certificate_number", "") if hatch_result else ""
    cert_path = getattr(hatch_result, "birth_certificate_path", "") if hatch_result else ""
    if cert_num:
        detail = cert_path if cert_path else cert_num
        table.add_row("Certificate", "[green]Ready[/green]", detail)
    else:
        table.add_row("Certificate", "[dim]Pending[/dim]", "Generated on hatch")

    # ── Windy Pro ──
    windy_pro_url = eco.get("windy_pro_url") or os.environ.get("WINDY_API_URL", "")
    windy_jwt = os.environ.get("WINDY_JWT", "")
    if windy_pro_url and windy_jwt:
        table.add_row("Windy Pro", "[green]Linked[/green]", "Recordings + translations")
    elif windy_pro_url:
        table.add_row("Windy Pro", "[yellow]Configured[/yellow]", windy_pro_url)
    else:
        table.add_row("Windy Pro", "[dim]N/A[/dim]", "Set ecosystem.windy_pro_url to connect")

    # ── Windy Cloud ──
    cloud_url = eco.get("windy_cloud_url") or os.environ.get("WINDY_CLOUD_URL", "")
    if cloud_url:
        table.add_row("Windy Cloud", "[green]Linked[/green]", "Backup + sync available")
    else:
        table.add_row("Windy Cloud", "[dim]N/A[/dim]", "Set ecosystem.windy_cloud_url to enable")

    console.print()
    if not has_real_services:
        console.print("  [yellow]\u26a0\ufe0f  Running in local mode (no ecosystem services configured)[/yellow]")
        console.print("  [dim]Set URLs in windyfly.toml [ecosystem] section to connect to real services[/dim]")
        console.print()
    console.print(table)
    console.print()
