"""Digital birth certificate generation for Windy Fly agents.

Generates a PDF and terminal-rendered birth certificate at hatch time,
including a neural fingerprint, first words, and waveform signature.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


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
    email_address: str = ""
    phone_number: str = ""
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
    hatch_timezone: str = "UTC",
) -> BirthCertificate:
    """Generate a complete birth certificate for a newly hatched agent."""
    now = datetime.now(timezone.utc)
    timestamp_str = now.isoformat()

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
        email_address=email_address,
        phone_number=phone_number,
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
        f"  [dim]Certificate No: {cert.certificate_number}[/dim]",
        "",
        f"  [bold cyan]{cert.agent_name}[/bold cyan]",
        f"  Eternitas Passport: [green]{cert.passport_id}[/green]",
        "",
        f"  Born: {time_str} {cert.hatch_timezone}",
    ]

    if cert.owner_name:
        lines.append(f"  Owner: {cert.owner_name}")
    if cert.email_address:
        lines.append(f"  Email: {cert.email_address}")
    if cert.phone_number:
        lines.append(f"  Phone: {cert.phone_number}")
    if cert.model_id:
        lines.append(f"  Brain: {cert.model_id}")

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
    """Render a birth certificate as a PDF.

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
    pdf.cell(170, 6, "Windy Fly Agent Registry", align="C")

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
    y = 100
    pdf.set_font("Helvetica", "", 11)

    details = [
        ("Date of Hatch", cert.hatch_time.strftime("%d %B %Y at %H:%M:%S %Z") or cert.hatch_time.strftime("%d %B %Y at %H:%M:%S UTC")),
        ("Time Zone", cert.hatch_timezone or "UTC"),
    ]
    if cert.owner_name:
        details.append(("Owner", cert.owner_name))
    if cert.email_address:
        details.append(("Email", cert.email_address))
    if cert.phone_number:
        details.append(("Phone", cert.phone_number))
    if cert.model_id:
        details.append(("AI Brain", cert.model_id))
    if cert.machine_id:
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

    # Neural art
    art_lines = generate_neural_art(cert.neural_fingerprint, size=5)
    pdf.set_font("Courier", "", 12)
    for line in art_lines:
        pdf.set_xy(20, y)
        # Replace symbols with ASCII-safe alternatives for PDF
        safe_line = line
        for orig, repl in [
            ("◆", "#"), ("◇", "o"), ("●", "@"), ("○", "O"),
            ("■", "H"), ("□", "="), ("▲", "A"), ("△", "V"),
            ("★", "*"), ("☆", "+"), ("◈", "X"), ("◉", "Q"),
            ("⬡", "Y"), ("⬢", "W"), ("⏣", "M"), ("⎔", "D"),
        ]:
            safe_line = safe_line.replace(orig, repl)
        pdf.cell(170, 6, safe_line, align="C")
        y += 6

    y += 5

    # First Words section
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_xy(20, y)
    pdf.cell(170, 8, "First Words", align="C")
    y += 10

    pdf.set_font("Helvetica", "I", 10)
    first_words = cert.first_words
    if len(first_words) > 200:
        first_words = first_words[:197] + "..."
    pdf.set_xy(35, y)
    pdf.multi_cell(140, 6, f'"{first_words}"', align="C")
    y = pdf.get_y() + 5

    # Waveform signature section
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_xy(20, y)
    pdf.cell(170, 8, "Waveform Signature", align="C")
    y += 10

    pdf.set_font("Courier", "", 10)
    # Replace Unicode bars with ASCII-safe equivalents
    safe_wave = cert.waveform_signature
    for orig, repl in [
        ("█", "|"), ("▆", "I"), ("▅", "I"), ("▃", ":"),
        ("▂", "."), ("▁", "."), ("·", " "),
    ]:
        safe_wave = safe_wave.replace(orig, repl)
    pdf.set_xy(20, y)
    pdf.cell(170, 6, safe_wave, align="C")

    # Footer
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.set_xy(20, 265)
    pdf.cell(170, 5, "Issued by the Windy Fly Agent Registry | eternitas.ai", align="C")

    return pdf.output()


def save_birth_certificate(cert: BirthCertificate, directory: str = "data") -> str:
    """Save the birth certificate as a PDF and return the file path."""
    pdf_bytes = render_birth_certificate_pdf(cert)
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)

    filename = f"birth_certificate_{cert.passport_id}.pdf"
    filepath = path / filename
    filepath.write_bytes(pdf_bytes)

    cert.pdf_path = str(filepath)
    return str(filepath)
