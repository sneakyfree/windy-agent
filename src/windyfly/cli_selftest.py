"""``windy test`` / ``windy selftest`` — agent self-test + ecosystem health.

Two modes, driven by ``--full``:

* Default (no flag) — the local-only pipeline check: send a message to
  the agent loop, verify the response round-trips through episodes + the
  cost ledger.

* ``--full`` — also dispatches HTTP health checks to every ecosystem
  platform the agent depends on (Eternitas, Windy Pro, Matrix, Windy
  Mail, Windy Cloud) with a configurable timeout. Exits non-zero if any
  *critical* dependency is red; warnings-only for non-critical (e.g.
  Chat homeserver unreachable when Matrix isn't configured).

DEPLOY.md §5 references this; the Wave 9 smoke script calls it via
``windy test --full`` / ``windy selftest --full``.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Iterable

from rich.console import Console

console = Console()


# ─── Ecosystem health ────────────────────────────────────────────────


@dataclass
class EcosystemCheck:
    """One dispatched HTTP health check."""

    name: str
    url: str
    critical: bool
    # populated after the HTTP call:
    ok: bool = False
    latency_ms: int = 0
    detail: str = ""


def _build_ecosystem_checks() -> list[EcosystemCheck]:
    """Compose the list of endpoints to probe based on env configuration.

    We only enqueue a check if the relevant env var is set — running
    `--full` on a machine that never configured Matrix shouldn't fail on
    Matrix. Critical services (Eternitas, Windy Pro) fail the run;
    optional channels (Matrix, Mail, Cloud) surface as warnings.
    """
    checks: list[EcosystemCheck] = []

    # Eternitas — /registry/verify/{passport} when we have one, else /health.
    eternitas_base = (
        os.environ.get("ETERNITAS_API_URL")
        or os.environ.get("ETERNITAS_URL", "")
    ).rstrip("/")
    if eternitas_base:
        passport = os.environ.get("ETERNITAS_PASSPORT", "").strip()
        if passport:
            url = f"{eternitas_base}/api/v1/registry/verify/{passport}"
        else:
            url = f"{eternitas_base}/health"
        checks.append(EcosystemCheck(name="Eternitas", url=url, critical=True))

    # Windy Pro — /healthz.
    pro_base = (
        os.environ.get("WINDY_PRO_URL") or os.environ.get("WINDY_API_URL", "")
    ).rstrip("/")
    if pro_base:
        checks.append(EcosystemCheck(
            name="Windy Pro", url=f"{pro_base}/healthz", critical=True,
        ))

    # Matrix — client-server version endpoint is the canonical liveness probe.
    matrix = os.environ.get("MATRIX_HOMESERVER", "").rstrip("/")
    if matrix:
        checks.append(EcosystemCheck(
            name="Windy Chat", url=f"{matrix}/_matrix/client/versions", critical=False,
        ))

    # Windy Mail — /healthz.
    mail = os.environ.get("WINDYMAIL_API_URL", "").rstrip("/")
    if mail:
        checks.append(EcosystemCheck(
            name="Windy Mail", url=f"{mail}/healthz", critical=False,
        ))

    # Windy Cloud — /healthz.
    cloud = os.environ.get("WINDY_CLOUD_URL", "").rstrip("/")
    if cloud:
        checks.append(EcosystemCheck(
            name="Windy Cloud", url=f"{cloud}/healthz", critical=False,
        ))

    return checks


def _dispatch_checks(checks: Iterable[EcosystemCheck], timeout: float) -> None:
    """Run each check in sequence, populating ok/latency/detail in place."""
    try:
        import httpx
    except ImportError:
        for c in checks:
            c.detail = "httpx not installed — install windyfly's base deps"
        return

    for c in checks:
        start = time.monotonic()
        try:
            resp = httpx.get(c.url, timeout=timeout)
            c.latency_ms = int((time.monotonic() - start) * 1000)
            # /health endpoints (and the Matrix versions endpoint) all
            # return 200 on a healthy service. Eternitas's
            # /registry/verify/{passport} returns 200 on known, 404 on
            # unknown — a 404 still proves the service is alive, so we
            # treat <500 as "service reachable". Callers can look at the
            # detail string to see the exact status.
            c.ok = resp.status_code < 500
            c.detail = f"HTTP {resp.status_code}"
        except Exception as exc:
            c.latency_ms = int((time.monotonic() - start) * 1000)
            c.ok = False
            c.detail = f"{type(exc).__name__}: {exc}"


def _render_ecosystem_table(checks: list[EcosystemCheck]) -> bool:
    """Render results and return True iff every CRITICAL check passed."""
    from rich.table import Table

    if not checks:
        console.print("  [dim]No ecosystem endpoints configured — skipping.[/dim]")
        console.print()
        return True

    table = Table(
        title="Ecosystem health",
        title_style="bold",
        border_style="cyan",
        show_lines=False,
    )
    table.add_column("Service", style="bold", min_width=12)
    table.add_column("Endpoint", overflow="fold")
    table.add_column("Status")
    table.add_column("Latency", justify="right")

    all_critical_ok = True
    for c in checks:
        if c.ok:
            status = "[green]PASS[/green]"
        elif c.critical:
            status = "[red]FAIL[/red] [dim](critical)[/dim]"
            all_critical_ok = False
        else:
            status = "[yellow]WARN[/yellow]"
        table.add_row(c.name, c.url, f"{status}  [dim]{c.detail}[/dim]", f"{c.latency_ms}ms")

    console.print()
    console.print(table)
    console.print()
    return all_critical_ok


def run_ecosystem_health(*, timeout: float = 5.0) -> bool:
    """Dispatch configured ecosystem health checks. Returns True iff every
    critical dependency was reachable."""
    console.print("[bold cyan]🪰 Ecosystem health[/bold cyan]")
    checks = _build_ecosystem_checks()
    _dispatch_checks(checks, timeout=timeout)
    return _render_ecosystem_table(checks)


# ─── --full entry point ──────────────────────────────────────────────


def run_full_self_test(*, timeout: float = 5.0) -> None:
    """Base self-test + ecosystem health.

    Exits non-zero if either the base self-test fails or any critical
    ecosystem dependency is unreachable. **The ecosystem phase always
    runs**, even when the base self-test has failed — the two diagnose
    orthogonal problems (the base test needs an LLM, the health phase
    doesn't), and a red base test is exactly when operators most need
    to know whether the ecosystem is reachable.
    """
    base_ok = run_self_test(exit_on_failure=False)
    critical_ok = run_ecosystem_health(timeout=timeout)

    if not base_ok:
        console.print("  [bold red]✗ Base self-test failed[/bold red]")
    if not critical_ok:
        console.print("  [bold red]✗ Critical ecosystem dependency unreachable[/bold red]")

    if base_ok and critical_ok:
        console.print("  [bold green]✓ All checks passed — agent + ecosystem green[/bold green]")
        console.print()
        return

    console.print()
    sys.exit(1)


def run_self_test(*, exit_on_failure: bool = True) -> bool:
    """Run the agent self-test. Returns True iff every check passed.

    Steps:
        1. Send "What is 2+2?" to the agent loop
        2. Verify a response is returned (non-empty string)
        3. Check the response was saved to episodes table
        4. Check cost was logged to cost_ledger
        5. Print pass/fail summary

    ``exit_on_failure`` preserves the historical ``windy test`` UX
    (sys.exit(1) on failure). Callers composing selftest into a
    multi-phase flow (e.g. ``run_full_self_test``) pass False so they
    can run the other phases regardless.
    """
    console.print()
    console.print("[bold cyan]🪰 Windy Fly Self-Test[/bold cyan]")
    console.print()

    passed = 0
    failed = 0
    details: list[tuple[str, bool, str]] = []

    # ── Setup ──
    try:
        from dotenv import load_dotenv
        load_dotenv()

        from windyfly.config import load_config
        config = load_config()
    except Exception as e:
        console.print(f"  [red]✗ Setup failed:[/red] {e}")
        console.print()
        console.print("  [dim]Run [bold]windy doctor[/bold] to diagnose.[/dim]")
        if exit_on_failure:
            sys.exit(1)
        return False

    db_path = config.get("memory", {}).get("db_path", "data/windyfly.db")

    try:
        from windyfly.memory.database import Database
        from windyfly.memory.write_queue import WriteQueue

        db = Database(db_path)
        write_queue = WriteQueue()
        write_queue.start()
    except Exception as e:
        console.print(f"  [red]✗ Database/WriteQueue init failed:[/red] {e}")
        if exit_on_failure:
            sys.exit(1)
        return False

    session_id = f"selftest-{uuid.uuid4().hex[:8]}"
    test_message = "What is 2+2?"

    # ── Step 1: Send message and get response ──
    try:
        from windyfly.agent.loop import agent_respond
        from windyfly.tools.registry import ToolRegistry

        tool_registry = ToolRegistry()

        start = time.time()
        response = agent_respond(
            config, db, write_queue, test_message, session_id, tool_registry,
        )
        elapsed = time.time() - start

        if response and len(response.strip()) > 0:
            details.append(("Response received", True, f'"{response[:80]}..." ({elapsed:.1f}s)'))
            passed += 1
        else:
            details.append(("Response received", False, "Empty response"))
            failed += 1
    except Exception as e:
        details.append(("Response received", False, str(e)))
        failed += 1
        response = None

    # ── Step 2: Wait for write queue to flush ──
    time.sleep(1)
    write_queue.stop()
    time.sleep(0.5)

    # ── Step 3: Check episodes table ──
    try:
        rows = db.fetchall(
            "SELECT role, content FROM episodes WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        user_saved = any(r["role"] == "user" and test_message in r["content"] for r in rows)
        assistant_saved = any(r["role"] == "assistant" and len(r["content"]) > 0 for r in rows)

        if user_saved and assistant_saved:
            details.append(("Episodes saved", True, f"{len(rows)} episodes in DB"))
            passed += 1
        else:
            missing = []
            if not user_saved:
                missing.append("user message")
            if not assistant_saved:
                missing.append("assistant response")
            details.append(("Episodes saved", False, f"Missing: {', '.join(missing)}"))
            failed += 1
    except Exception as e:
        details.append(("Episodes saved", False, str(e)))
        failed += 1

    # ── Step 4: Check cost_ledger ──
    try:
        cost_row = db.fetchone(
            "SELECT * FROM cost_ledger ORDER BY created_at DESC LIMIT 1"
        )
        if cost_row and cost_row.get("cost_usd", 0) >= 0:
            cost = cost_row["cost_usd"]
            model = cost_row.get("model", "unknown")
            details.append(("Cost logged", True, f"${cost:.6f} ({model})"))
            passed += 1
        else:
            details.append(("Cost logged", False, "No cost entry found"))
            failed += 1
    except Exception as e:
        details.append(("Cost logged", False, str(e)))
        failed += 1

    # ── Step 5: Check "4" appears in response ──
    if response:
        has_answer = "4" in response
        details.append(("Correct answer", has_answer,
                        "Contains '4'" if has_answer else "Missing '4' in response"))
        if has_answer:
            passed += 1
        else:
            failed += 1
    else:
        details.append(("Correct answer", False, "No response to check"))
        failed += 1

    # ── Results ──
    console.print()
    for label, ok, detail in details:
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {icon} [bold]{label}:[/bold] {detail}")

    console.print()

    if failed == 0:
        console.print(f"  [bold green]Self-test passed ✓[/bold green]  ({passed}/{passed + failed} checks)")
    else:
        console.print(f"  [bold red]Self-test failed ✗[/bold red]  ({passed}/{passed + failed} checks passed)")
        console.print()
        console.print("  [dim]Run [bold]windy doctor[/bold] to diagnose issues.[/dim]")

    console.print()
    db.close()

    if failed > 0:
        if exit_on_failure:
            sys.exit(1)
        return False
    return True
