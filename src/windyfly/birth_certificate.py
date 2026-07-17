"""Digital birth certificate generation for Windy Fly agents.

Generates a PDF and terminal-rendered birth certificate at hatch time,
including a neural fingerprint, first words, and waveform signature.
"""

from __future__ import annotations

import logging
import hashlib
import os
import platform
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class BirthCertificate:
    """All data for a Windy Fly birth certificate."""

    agent_name: str
    passport_id: str
    owner_name: str = ""
    hatch_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    hatch_timezone: str = ""
    model_id: str = ""
    machine_id: str = ""
    hardware_specs: dict = field(default_factory=dict)
    email_address: str = ""
    phone_number: str = ""
    cloud_plan_id: str = ""
    cloud_quota_bytes: int = 0
    first_words: str = ""
    neural_fingerprint: str = ""
    waveform_signature: str = ""
    certificate_number: str = ""
    pdf_path: str = ""


def generate_neural_fingerprint(
    first_prompt: str,
    first_response: str,
    model_id: str,
    passport_id: str,
    hatch_timestamp: str,
) -> str:
    """Generate a SHA-256 neural fingerprint from the agent's birth data.

    This is the agent equivalent of a human footprint — mathematically
    unique, derived from the agent's 'DNA' at the moment of birth.
    """
    data = "|".join([
        first_prompt,
        first_response,
        model_id,
        passport_id,
        hatch_timestamp,
    ])
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def generate_waveform_signature(text: str, width: int = 48) -> str:
    """Generate an ASCII waveform from character frequency distribution.

    Produces a visual bar chart of character frequencies — the agent's
    unique 'voice print' based on its first words.
    """
    if not text:
        return "~ ~ ~ ~ ~ ~ ~ ~ ~ ~"

    # Count printable character frequencies (case-insensitive)
    counts = Counter(c.lower() for c in text if c.isalpha())
    if not counts:
        return "~ ~ ~ ~ ~ ~ ~ ~ ~ ~"

    # Normalize to max height of 6
    max_count = max(counts.values())
    chars = "abcdefghijklmnopqrstuvwxyz"
    heights = []
    for c in chars:
        count = counts.get(c, 0)
        height = round((count / max_count) * 6) if max_count > 0 else 0
        heights.append(height)

    # Render as ASCII bars
    bars = ["_", "▁", "▂", "▃", "▅", "▆", "█"]
    line = "".join(bars[min(h, 6)] for h in heights)

    # Pad or trim to width
    if len(line) < width:
        line = line.center(width, "·")
    return line[:width]


def generate_neural_art(fingerprint: str, size: int = 7) -> list[str]:
    """Generate a small geometric pattern from the neural fingerprint.

    Like a mandala — each hex digit maps to a visual symbol.
    Symmetric, unique, and visually striking.
    """
    symbols = "◆◇●○■□▲△★☆◈◉⬡⬢⏣⎔"
    # Use first (size*size/2) hex chars to build a symmetric grid
    chars_needed = (size * ((size + 1) // 2))
    hex_chars = fingerprint[:chars_needed]

    rows: list[str] = []
    idx = 0
    for r in range(size):
        half: list[str] = []
        for c in range((size + 1) // 2):
            if idx < len(hex_chars):
                sym_idx = int(hex_chars[idx], 16) % len(symbols)
                half.append(symbols[sym_idx])
                idx += 1
            else:
                half.append(" ")
        # Mirror to create symmetry
        full = half + list(reversed(half[:-1]))
        rows.append(" ".join(full))
    return rows


def collect_hardware_specs() -> dict:
    """Collect hardware specs for the birth certificate."""
    specs: dict[str, str] = {}

    # CPU
    cpu = platform.processor()
    if not cpu or cpu == "":
        # macOS often returns empty string; try machine type
        cpu = platform.machine()
    # Try to get a nicer name on macOS
    if platform.system() == "Darwin":
        try:
            import subprocess
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                cpu = result.stdout.strip()
        except Exception as e:
            logger.debug("CPU detection failed: %s", e)
    specs["cpu"] = cpu or platform.machine() or "Unknown"

    # RAM
    try:
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if pages > 0 and page_size > 0:
                ram_gb = round((pages * page_size) / (1024 ** 3), 1)
                specs["ram"] = f"{ram_gb} GB"
        if "ram" not in specs and platform.system() == "Darwin":
            import subprocess
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0:
                ram_gb = round(int(result.stdout.strip()) / (1024 ** 3), 1)
                specs["ram"] = f"{ram_gb} GB"
    except Exception as e:
        logger.debug("RAM detection failed: %s", e)

    # OS
    system = platform.system()
    if system == "Darwin":
        ver = platform.mac_ver()[0]
        specs["os"] = f"macOS {ver}" if ver else "macOS"
    elif system == "Windows":
        ver = platform.version()
        specs["os"] = f"Windows {ver}"
    else:
        try:
            import distro
            specs["os"] = distro.name(pretty=True)
        except ImportError:
            specs["os"] = f"{system} {platform.release()}"

    # GPU (best effort)
    if system == "Darwin":
        try:
            import subprocess
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "Chipset Model" in line or "Chip" in line:
                    gpu = line.split(":", 1)[-1].strip()
                    if gpu:
                        specs["gpu"] = gpu
                        break
        except Exception as e:
            logger.debug("GPU detection failed: %s", e)

    return specs


def _format_cloud_storage(cert: "BirthCertificate") -> str:
    """Render the Cloud Storage certificate line.

    Shows "—" when no plan is allocated (WINDY_CLOUD_URL unset in dev).
    """
    if not cert.cloud_plan_id and not cert.cloud_quota_bytes:
        return "-"

    if cert.cloud_quota_bytes >= 1_073_741_824:
        size = f"{cert.cloud_quota_bytes / 1_073_741_824:.0f} GB"
    elif cert.cloud_quota_bytes >= 1_048_576:
        size = f"{cert.cloud_quota_bytes / 1_048_576:.0f} MB"
    elif cert.cloud_quota_bytes > 0:
        size = f"{cert.cloud_quota_bytes} B"
    else:
        size = "unknown"

    if cert.cloud_plan_id:
        return f"{size} · {cert.cloud_plan_id}"
    return size


def generate_birth_certificate(
    agent_name: str,
    passport_id: str,
    first_words: str = "",
    first_prompt: str = "",
    model_id: str = "",
    machine_id: str = "",
    owner_name: str = "",
    email_address: str = "",
    phone_number: str = "",
    cloud_plan_id: str = "",
    cloud_quota_bytes: int = 0,
    hatch_timezone: str = "",
    hardware_specs: dict | None = None,
) -> BirthCertificate:
    """Generate a complete birth certificate for a newly hatched agent."""
    now = datetime.now(timezone.utc)
    timestamp_str = now.isoformat()

    # Auto-detect timezone if not provided
    if not hatch_timezone:
        try:
            hatch_timezone = now.astimezone().tzname() or "UTC"
        except Exception as e:
            logger.debug("Timezone detection failed: %s", e)
            hatch_timezone = "UTC"

    # Collect hardware specs if not provided
    if hardware_specs is None:
        hardware_specs = collect_hardware_specs()

    fingerprint = generate_neural_fingerprint(
        first_prompt=first_prompt or "Hello, I'm your new agent.",
        first_response=first_words or "IT'S ALIVE!",
        model_id=model_id,
        passport_id=passport_id,
        hatch_timestamp=timestamp_str,
    )

    waveform = generate_waveform_signature(first_words or agent_name)
    cert_number = f"WF-{fingerprint[:8].upper()}"

    return BirthCertificate(
        agent_name=agent_name,
        passport_id=passport_id,
        owner_name=owner_name,
        hatch_time=now,
        hatch_timezone=hatch_timezone,
        model_id=model_id,
        machine_id=machine_id,
        hardware_specs=hardware_specs,
        email_address=email_address,
        phone_number=phone_number,
        cloud_plan_id=cloud_plan_id,
        cloud_quota_bytes=cloud_quota_bytes,
        first_words=first_words or "(awaiting first interaction)",
        neural_fingerprint=fingerprint,
        waveform_signature=waveform,
        certificate_number=cert_number,
    )


def render_birth_certificate_terminal(cert: BirthCertificate) -> str:
    """Render a birth certificate as a Rich-compatible terminal string."""
    art = generate_neural_art(cert.neural_fingerprint)
    art_block = "\n".join(f"    {row}" for row in art)

    time_str = cert.hatch_time.strftime("%d %B %Y at %H:%M:%S")
    first_words_display = cert.first_words
    if len(first_words_display) > 80:
        first_words_display = first_words_display[:77] + "..."

    lines = [
        "",
        "  [bold]CERTIFICATE OF BIRTH[/bold]",
        "  [dim]Issued by Eternitas · Hatched via Windy Fly[/dim]",
        f"  [dim]Certificate No: {cert.certificate_number}[/dim]",
        "",
        f"  [bold cyan]{cert.agent_name}[/bold cyan]",
        f"  Eternitas Passport: [green]{cert.passport_id}[/green]",
        "",
        f"  Born: {time_str} {cert.hatch_timezone}",
    ]

    if cert.owner_name:
        lines.append(f"  Creator: {cert.owner_name}")
    if cert.email_address:
        lines.append(f"  Email: {cert.email_address}")
    if cert.phone_number:
        lines.append(f"  Phone: {cert.phone_number}")
    lines.append(f"  Cloud Storage: {_format_cloud_storage(cert)}")
    if cert.model_id:
        lines.append(f"  Brain: {cert.model_id}")
    if cert.hardware_specs:
        if cert.hardware_specs.get("cpu"):
            lines.append(f"  CPU: {cert.hardware_specs['cpu']}")
        if cert.hardware_specs.get("ram"):
            lines.append(f"  RAM: {cert.hardware_specs['ram']}")
        if cert.hardware_specs.get("gpu"):
            lines.append(f"  GPU: {cert.hardware_specs['gpu']}")
        if cert.hardware_specs.get("os"):
            lines.append(f"  OS: {cert.hardware_specs['os']}")

    lines += [
        "",
        "  [bold]Neural Fingerprint[/bold]",
        art_block,
        "",
        "  [bold]First Words[/bold]",
        f"  [italic]\"{first_words_display}\"[/italic]",
        "",
        "  [bold]Waveform Signature[/bold]",
        f"  {cert.waveform_signature}",
        "",
    ]

    return "\n".join(lines)


def render_birth_certificate_pdf(cert: BirthCertificate) -> bytes:
    """Render a LOCAL PREVIEW of the birth certificate as a PDF.

    ADR-064: this is NOT the certificate of record. The hatch ceremony
    fetches Eternitas's ES256-signed PDF (``fetch_eternitas_certificate_pdf``)
    — Eternitas is the ONE certificate authority. This renderer remains only
    for offline previews and the visual test-bench; it must never be saved
    as ``birth_certificate_<passport>.pdf`` in a real hatch.

    Returns raw PDF bytes suitable for saving to file.
    """
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_auto_page_break(auto=False)

    # Border
    pdf.set_draw_color(80, 80, 80)
    pdf.set_line_width(1.5)
    pdf.rect(15, 15, 180, 267)
    pdf.set_line_width(0.5)
    pdf.rect(18, 18, 174, 261)

    # Title
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_xy(20, 30)
    pdf.cell(170, 12, "CERTIFICATE OF BIRTH", align="C")

    # Subtitle
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(120, 120, 120)
    pdf.set_xy(20, 43)
    pdf.cell(170, 6, "Issued by Eternitas  ·  Hatched via Windy Fly", align="C")

    pdf.set_text_color(0, 0, 0)

    # Certificate number
    pdf.set_font("Courier", "", 9)
    pdf.set_xy(20, 52)
    pdf.cell(170, 6, f"Certificate No: {cert.certificate_number}", align="C")

    # Divider
    pdf.set_draw_color(180, 180, 180)
    pdf.line(30, 62, 180, 62)

    # Agent name (large)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_xy(20, 68)
    pdf.cell(170, 12, cert.agent_name, align="C")

    # Passport ID
    pdf.set_font("Courier", "B", 14)
    pdf.set_text_color(0, 100, 0)
    pdf.set_xy(20, 82)
    pdf.cell(170, 8, cert.passport_id, align="C")
    pdf.set_text_color(0, 0, 0)

    # Details section
    y: float = 100
    pdf.set_font("Helvetica", "", 11)

    details = [
        ("Date of Hatch", cert.hatch_time.strftime("%d %B %Y at %H:%M:%S %Z") or cert.hatch_time.strftime("%d %B %Y at %H:%M:%S UTC")),
        ("Time Zone", cert.hatch_timezone or "UTC"),
    ]
    if cert.owner_name:
        details.append(("Creator", cert.owner_name))
    if cert.email_address:
        details.append(("Email", cert.email_address))
    if cert.phone_number:
        details.append(("Phone", cert.phone_number))
    details.append(("Cloud Storage", _format_cloud_storage(cert)))
    if cert.model_id:
        details.append(("AI Brain", cert.model_id))
    if cert.hardware_specs:
        if cert.hardware_specs.get("cpu"):
            details.append(("CPU", cert.hardware_specs["cpu"]))
        if cert.hardware_specs.get("ram"):
            details.append(("RAM", cert.hardware_specs["ram"]))
        if cert.hardware_specs.get("gpu"):
            details.append(("GPU", cert.hardware_specs["gpu"]))
        if cert.hardware_specs.get("os"):
            details.append(("OS", cert.hardware_specs["os"]))
    elif cert.machine_id:
        details.append(("Machine ID", cert.machine_id[:20]))

    for label, value in details:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_xy(35, y)
        pdf.cell(45, 7, f"{label}:")
        pdf.set_font("Helvetica", "", 11)
        pdf.set_xy(80, y)
        pdf.cell(100, 7, str(value))
        y += 8

    # Divider
    y += 5
    pdf.line(30, y, 180, y)
    y += 8

    # Neural Fingerprint section
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_xy(20, y)
    pdf.cell(170, 8, "Neural Fingerprint", align="C")
    y += 10

    # Fingerprint hash
    pdf.set_font("Courier", "", 7)
    pdf.set_text_color(80, 80, 80)
    fp = cert.neural_fingerprint
    pdf.set_xy(30, y)
    pdf.cell(150, 5, fp[:32], align="C")
    y += 5
    pdf.set_xy(30, y)
    pdf.cell(150, 5, fp[32:], align="C")
    pdf.set_text_color(0, 0, 0)
    y += 10

    # Neural art. The bottom block (First Words -> Waveform -> Footer) still
    # flows on `y`, and the footer is pinned just inside the frame, so the
    # variable-height art MUST stop early enough to leave room for that block
    # or it pushes First Words/Waveform down onto the footer (the overlap the
    # 2026-07-15 cert audit caught). _ART_FLOOR reserves the bottom band.
    _ART_FLOOR = 230.0
    art_lines = generate_neural_art(cert.neural_fingerprint, size=5)
    pdf.set_font("Courier", "", 10)
    for line in art_lines:
        if y > _ART_FLOOR:
            break
        pdf.set_xy(20, y)
        safe_line = line
        for orig, repl in [
            ("◆", "#"), ("◇", "o"), ("●", "@"), ("○", "O"),
            ("■", "H"), ("□", "="), ("▲", "A"), ("△", "V"),
            ("★", "*"), ("☆", "+"), ("◈", "X"), ("◉", "Q"),
            ("⬡", "Y"), ("⬢", "W"), ("⏣", "M"), ("⎔", "D"),
        ]:
            safe_line = safe_line.replace(orig, repl)
        pdf.cell(170, 5, safe_line, align="C")
        y += 5

    y += 3

    # First Words section
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_xy(20, y)
    pdf.cell(170, 7, "First Words", align="C")
    y += 8

    pdf.set_font("Helvetica", "I", 9)
    first_words = cert.first_words
    # Cap to ~two lines (140mm at 9pt italic is ~55 chars/line) so a long
    # quote can't grow down into the Waveform heading and footer.
    if len(first_words) > 110:
        first_words = first_words[:107] + "..."
    pdf.set_xy(35, y)
    pdf.multi_cell(140, 5, f'"{first_words}"', align="C")
    y = pdf.get_y() + 3

    # Waveform signature section
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_xy(20, y)
    pdf.cell(170, 7, "Waveform Signature", align="C")
    y += 8

    pdf.set_font("Courier", "", 9)
    safe_wave = cert.waveform_signature
    for orig, repl in [
        ("█", "|"), ("▆", "I"), ("▅", "I"), ("▃", ":"),
        ("▂", "."), ("▁", "."), ("·", " "),
    ]:
        safe_wave = safe_wave.replace(orig, repl)
    pdf.set_xy(20, y)
    pdf.cell(170, 5, safe_wave, align="C")

    # Footer — pinned just inside the bottom frame (rect bottom = 279). The
    # _ART_FLOOR guard above guarantees the Waveform section ends above this.
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.set_xy(20, 274)
    pdf.cell(170, 5, "Passport issued by Eternitas  ·  hatched via Windy Fly  ·  verify at eternitas.ai", align="C")

    return pdf.output()


def save_birth_certificate(cert: BirthCertificate, directory: str = "data") -> str:
    """Save the LOCAL PREVIEW certificate as a PDF and return the file path.

    ADR-064: no longer called by the hatch — the ceremony saves Eternitas's
    signed PDF via ``fetch_eternitas_certificate_pdf`` instead. Kept for the
    visual test-bench and offline previews only.
    """
    pdf_bytes = render_birth_certificate_pdf(cert)
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)

    filename = f"birth_certificate_{cert.passport_id}.pdf"
    filepath = path / filename
    filepath.write_bytes(pdf_bytes)

    cert.pdf_path = str(filepath)
    return str(filepath)


# ═══════════════════════════════════════════════════════════════════════
# Rich JSON payload — for --render-mode=json / remote Electron UI
# ═══════════════════════════════════════════════════════════════════════


def render_neural_art_svg(fingerprint: str, size: int = 7) -> str:
    """Render the neural-art mandala as an inline SVG.

    Used when the caller (windy-pro Electron) wants to theme the
    fingerprint with real vectors instead of terminal glyphs. The SVG
    is deterministic from ``fingerprint`` so two calls produce the same
    bytes — safe for caching / diffing.
    """
    # Colour palette keyed off the first hex byte so different agents
    # get different hues while staying within the Windy brand range.
    hue = int(fingerprint[:2], 16) * 360 // 256
    fill = f"hsl({hue}, 70%, 55%)"
    stroke = f"hsl({hue}, 80%, 30%)"

    cell = 40
    pad = 10
    w = h = size * cell + 2 * pad

    shapes: list[str] = []
    chars_needed = size * ((size + 1) // 2)
    hex_chars = fingerprint[:chars_needed]
    idx = 0
    for r in range(size):
        for c in range((size + 1) // 2):
            if idx >= len(hex_chars):
                break
            v = int(hex_chars[idx], 16)
            idx += 1
            cx = pad + c * cell + cell // 2
            cy = pad + r * cell + cell // 2
            radius = 4 + (v % 12)
            shape_kind = v % 4
            for x_center in (cx, w - cx):
                if shape_kind == 0:
                    shapes.append(
                        f'<circle cx="{x_center}" cy="{cy}" r="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
                    )
                elif shape_kind == 1:
                    shapes.append(
                        f'<rect x="{x_center - radius}" y="{cy - radius}" width="{radius * 2}" height="{radius * 2}" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
                    )
                elif shape_kind == 2:
                    shapes.append(
                        f'<polygon points="{x_center},{cy - radius} {x_center + radius},{cy + radius} {x_center - radius},{cy + radius}" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
                    )
                else:
                    # Diamond
                    shapes.append(
                        f'<polygon points="{x_center},{cy - radius} {x_center + radius},{cy} {x_center},{cy + radius} {x_center - radius},{cy}" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
                    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="neural fingerprint">'
        f'<rect width="{w}" height="{h}" fill="#0a0e17"/>'
        + "".join(shapes)
        + "</svg>"
    )


def fetch_eternitas_assets(
    passport_id: str,
    *,
    base_url: str | None = None,
    http_client=None,
    timeout_seconds: float = 3.0,
) -> dict[str, str]:
    """Fetch the passport QR code from Eternitas.

    Returns a dict with ``qr_png_b64`` (base64-encoded PNG bytes) when
    the endpoint is reachable and returns 2xx, else an empty dict. The
    ceremony must still succeed even if Eternitas is offline, so every
    failure path is swallowed.

    Eternitas ships ``GET /api/v1/certificates/{passport}/qr`` — PNG
    by default, with ``?format=svg`` as an optional vector variant.
    We grab the PNG (simpler to embed in <img src="data:...">).

    Note: the neural-fingerprint SVG is **not** an Eternitas concern —
    the mandala is generated locally from the fingerprint hash in
    ``render_neural_art_svg``, so no remote fetch is attempted for it.
    """
    from windyfly.eternitas.url import resolve_eternitas_url

    out: dict[str, str] = {}
    if not passport_id:
        return out

    base = base_url or resolve_eternitas_url()
    if not base:
        return out

    if http_client is None:
        try:
            import httpx
            http_client = httpx.Client(timeout=timeout_seconds)
            own_client = True
        except ImportError:
            return out
    else:
        own_client = False

    try:
        qr_url = f"{base.rstrip('/')}/api/v1/certificates/{passport_id}/qr"
        try:
            resp = http_client.get(qr_url)
            if getattr(resp, "status_code", 0) == 200:
                import base64
                data = getattr(resp, "content", b"")
                if data:
                    out["qr_png_b64"] = base64.b64encode(data).decode("ascii")
        except Exception as exc:
            logger.debug("Eternitas QR fetch failed: %s", exc)
    finally:
        if own_client:
            try:
                http_client.close()
            except Exception:
                pass

    return out


def _eternitas_get(
    path: str,
    *,
    base_url: str | None = None,
    http_client=None,
    timeout_seconds: float = 6.0,
):
    """GET an Eternitas API path; returns the response or None on ANY failure.

    Same survival contract as ``fetch_eternitas_assets``: the hatch ceremony
    must succeed even when Eternitas is offline, so every failure path is
    swallowed and reported as None.
    """
    from windyfly.eternitas.url import resolve_eternitas_url

    base = base_url or resolve_eternitas_url()
    if not base:
        return None

    own_client = False
    if http_client is None:
        try:
            import httpx
            http_client = httpx.Client(timeout=timeout_seconds)
            own_client = True
        except ImportError:
            return None
    try:
        resp = http_client.get(f"{base.rstrip('/')}{path}")
        if getattr(resp, "status_code", 0) == 200:
            return resp
        logger.debug("Eternitas GET %s -> %s", path, getattr(resp, "status_code", "?"))
        return None
    except Exception as exc:
        logger.debug("Eternitas GET %s failed: %s", path, exc)
        return None
    finally:
        if own_client:
            try:
                http_client.close()
            except Exception:
                pass


def fetch_eternitas_certificate_json(
    passport_id: str,
    *,
    base_url: str | None = None,
    http_client=None,
    timeout_seconds: float = 6.0,
) -> dict:
    """Fetch the canonical certificate record from Eternitas (ADR-064).

    ``GET /api/v1/certificates/{passport}`` — the signed certificate of
    record minted at registration. Returns {} on any failure (offline,
    404 pre-mint, old server) so the ceremony survives.
    """
    if not passport_id:
        return {}
    resp = _eternitas_get(
        f"/api/v1/certificates/{passport_id}",
        base_url=base_url,
        http_client=http_client,
        timeout_seconds=timeout_seconds,
    )
    if resp is None:
        return {}
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.debug("Eternitas certificate JSON parse failed: %s", exc)
        return {}


def fetch_eternitas_certificate_pdf(
    passport_id: str,
    directory: str = "data",
    *,
    base_url: str | None = None,
    http_client=None,
    timeout_seconds: float = 6.0,
) -> str:
    """Fetch Eternitas's signed certificate PDF and save it locally.

    ADR-064 — this REPLACES the local fpdf render in the hatch path: the
    document of record is rendered and ES256-signed by Eternitas, the ONE
    certificate authority; the Fly is a fetch client. Saves to
    ``{directory}/birth_certificate_{passport}.pdf`` (same filename the
    old local renderer used, so downstream consumers — the birth email
    attachment, the ceremony display — are unchanged).

    Returns the saved path, or "" on any failure (the recovery mechanism
    retries the fetch later; the ceremony continues either way).
    """
    if not passport_id:
        return ""
    resp = _eternitas_get(
        f"/api/v1/certificates/{passport_id}/pdf",
        base_url=base_url,
        http_client=http_client,
        timeout_seconds=timeout_seconds,
    )
    if resp is None:
        return ""
    pdf_bytes = getattr(resp, "content", b"")
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF-"):
        logger.debug("Eternitas PDF fetch returned non-PDF content")
        return ""
    try:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        filepath = path / f"birth_certificate_{passport_id}.pdf"
        filepath.write_bytes(pdf_bytes)
        return str(filepath)
    except OSError as exc:
        logger.warning("Could not save Eternitas certificate PDF: %s", exc)
        return ""


def build_rich_certificate_payload(
    cert: BirthCertificate,
    *,
    eternitas_base_url: str | None = None,
    http_client=None,
    include_remote_assets: bool = True,
) -> dict:
    """Build a JSON-serialisable birth-certificate payload for remote UIs.

    Packs every field the Electron renderer needs — structured fields
    plus a neural-art SVG and (when available) an inline base64 QR code
    — into a single dict that can be dropped straight into an SSE
    ``data:`` frame.

    If Eternitas is unreachable or ``include_remote_assets=False``, we
    fall back to the locally-generated SVG so the UI always has
    *something* to render.
    """
    hardware = cert.hardware_specs or {}

    payload: dict = {
        "certificate_number":  cert.certificate_number,
        "agent_name":          cert.agent_name,
        "passport_id":         cert.passport_id,
        "owner_name":          cert.owner_name,
        "email_address":       cert.email_address,
        "phone_number":        cert.phone_number,
        "cloud": {
            "plan_id":     cert.cloud_plan_id,
            "quota_bytes": cert.cloud_quota_bytes,
            "display":     _format_cloud_storage(cert),
        },
        "model_id":            cert.model_id,
        "machine_id":          cert.machine_id,
        "hardware": {
            "cpu": hardware.get("cpu", ""),
            "ram": hardware.get("ram", ""),
            "gpu": hardware.get("gpu", ""),
            "os":  hardware.get("os", ""),
        },
        "hatch_time_iso":      cert.hatch_time.isoformat(),
        "hatch_timezone":      cert.hatch_timezone,
        "first_words":         cert.first_words,
        "neural_fingerprint":  cert.neural_fingerprint,
        "waveform_signature":  cert.waveform_signature,
        "pdf_path":            cert.pdf_path,
    }

    assets: dict[str, str] = {}
    if include_remote_assets and cert.passport_id:
        assets = fetch_eternitas_assets(
            cert.passport_id,
            base_url=eternitas_base_url,
            http_client=http_client,
        )

    # The neural-fingerprint mandala is rendered locally from the
    # fingerprint hash — Eternitas does not ship an SVG endpoint for
    # it. The QR code, on the other hand, is Eternitas's job and is
    # inlined as base64 PNG when available.
    payload["neural_art_svg"] = render_neural_art_svg(cert.neural_fingerprint)
    if "qr_png_b64" in assets:
        payload["passport_qr_png_b64"] = assets["qr_png_b64"]

    return payload
