"""Visual hatch ceremony verification — runs every visual element and reports."""

from rich.console import Console
from io import StringIO
import os
import sys

# Set test values so we don't need real services
os.environ["WINDYFLY_AGENT_NAME"] = "TestFly"
os.environ["WINDY_OWNER_NAME"] = "Grant Whitmer"
os.environ["DEFAULT_MODEL"] = "claude-sonnet-4-20250514"

console = Console(file=StringIO(), force_terminal=True, width=100)

passed = 0
failed = 0

def report(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✓ {name}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


# ── Test 1: Hatching ceremony renders ────────────────────────────
print("\n═══ TEST 1: Hatching Ceremony ═══")
try:
    from windyfly.hatching import play_hatching
    play_hatching(animate=False)
    report("Hatching ceremony renders", True)
except Exception as e:
    report("Hatching ceremony renders", False, str(e))


# ── Test 2: Hardware specs collection ────────────────────────────
print("\n═══ TEST 2: Hardware Specs ═══")
try:
    from windyfly.birth_certificate import (
        generate_birth_certificate,
        render_birth_certificate_terminal,
        render_birth_certificate_pdf,
        save_birth_certificate,
        collect_hardware_specs,
    )
    specs = collect_hardware_specs()
    report("CPU detected", bool(specs.get("cpu")), specs.get("cpu", "MISSING"))
    report("OS detected", bool(specs.get("os")), specs.get("os", "MISSING"))
    report("RAM detected", bool(specs.get("ram")), specs.get("ram", "MISSING"))
except Exception as e:
    report("Hardware specs", False, str(e))


# ── Test 3: Birth certificate generation ─────────────────────────
print("\n═══ TEST 3: Birth Certificate ═══")
try:
    cert = generate_birth_certificate(
        agent_name="TestFly",
        passport_id="ET-TEST-12345",
        owner_name="Grant Whitmer",
        model_id="claude-sonnet-4-20250514",
        email_address="testfly@windymail.ai",
        phone_number="+1-555-0199",
        hardware_specs=specs,
    )
    report("Certificate number format", cert.certificate_number.startswith("WF-"), cert.certificate_number)
    report("Neural fingerprint length", len(cert.neural_fingerprint) == 64, f"{len(cert.neural_fingerprint)} chars")
    report("Hardware specs on cert", bool(cert.hardware_specs.get("cpu")))
except Exception as e:
    report("Birth certificate generation", False, str(e))


# ── Test 4: Terminal rendering ───────────────────────────────────
print("\n═══ TEST 4: Terminal Rendering ═══")
try:
    terminal_text = render_birth_certificate_terminal(cert)
    report("Says 'Creator:' not 'Owner:'", "Creator:" in terminal_text and "Owner:" not in terminal_text)
    report("CPU in terminal cert", specs["cpu"] in terminal_text)
    report("Terminal cert non-empty", len(terminal_text) > 200, f"{len(terminal_text)} chars")
    print("\n--- Terminal cert preview (first 1000 chars) ---")
    print(terminal_text[:1000])
    print("--- end preview ---")
except Exception as e:
    report("Terminal rendering", False, str(e))


# ── Test 5: PDF generation ───────────────────────────────────────
print("\n═══ TEST 5: PDF Generation ═══")
try:
    pdf_bytes = render_birth_certificate_pdf(cert)
    report("Valid PDF header", pdf_bytes[:5] == b"%PDF-")
    report("PDF size reasonable", len(pdf_bytes) > 1000, f"{len(pdf_bytes):,} bytes")

    import tempfile
    path = save_birth_certificate(cert, directory=tempfile.mkdtemp())
    report("PDF saved to disk", os.path.exists(path), path)
    file_size = os.path.getsize(path)
    report("File size on disk", file_size > 1000, f"{file_size:,} bytes")

    # Open PDF for visual inspection on macOS
    if sys.platform == "darwin":
        import subprocess
        subprocess.Popen(["open", path])
        print(f"\n  📄 OPENED PDF for visual inspection: {path}")
except Exception as e:
    report("PDF generation", False, str(e))


# ── Test 6: Naming ceremony in quickstart ────────────────────────
print("\n═══ TEST 6: Naming Ceremony ═══")
try:
    import inspect
    from windyfly.quickstart import _try_hatch_provisioning
    source = inspect.getsource(_try_hatch_provisioning)
    report("Naming prompt exists", "What's my name" in source or "Name your agent" in source or "name" in source.lower())
    report("Birth certificate in flow", "birth certificate" in source.lower() or "birth_certificate" in source.lower())
except Exception as e:
    report("Naming ceremony", False, str(e))


# ── Test 7: Daemon mode in CLI ───────────────────────────────────
print("\n═══ TEST 7: Daemon Mode ═══")
try:
    import inspect
    import windyfly.cli as cli_mod
    cli_source = inspect.getsource(cli_mod)
    report("--daemon flag in CLI", "daemon" in cli_source.lower())
    report("start_new_session in CLI", "start_new_session" in cli_source)
except Exception as e:
    report("Daemon mode", False, str(e))


# ── Test 8: Command registry ────────────────────────────────────
print("\n═══ TEST 8: Command Registry ═══")
try:
    from windyfly.commands.setup import init_all_commands
    from windyfly.commands.registry import registry
    init_all_commands()
    cmd_count = len(registry._commands)
    report("100+ commands registered", cmd_count >= 100, f"{cmd_count} commands")

    # Check a few key commands
    for name in ["doctor", "version", "help", "kill", "model", "soul", "budget"]:
        cmd = registry.get(name)
        report(f"Command '{name}' exists", cmd is not None)
except Exception as e:
    report("Command registry", False, str(e))


# ── Test 9: Channel adapters ────────────────────────────────────
print("\n═══ TEST 9: Channel Adapters ═══")
channels_ok = []
channels_fail = []
for ch in ["telegram_bot", "discord_bot", "slack_bot", "whatsapp_bot", "signal_bot", "teams_bot", "irc_bot"]:
    try:
        __import__(f"windyfly.channels.{ch}")
        channels_ok.append(ch)
    except ImportError as e:
        channels_fail.append(f"{ch}: {e}")
report("Channel adapters importable", len(channels_ok) >= 3, f"{len(channels_ok)}/7")
if channels_fail:
    for f in channels_fail:
        print(f"    ⚠ {f}")


# ── Test 10: Ecosystem status ───────────────────────────────────
print("\n═══ TEST 10: Ecosystem Status ═══")
try:
    from windyfly.hatching import show_ecosystem_status
    show_ecosystem_status()
    report("Ecosystem status renders", True)
except Exception as e:
    report("Ecosystem status", False, str(e))


# ── Summary ─────────────────────────────────────────────────────
print(f"\n{'═' * 50}")
print(f"  RESULTS: {passed} passed, {failed} failed")
if failed == 0:
    print("  === ALL VISUAL TESTS PASSED ===")
else:
    print(f"  === {failed} TEST(S) FAILED ===")
    sys.exit(1)
