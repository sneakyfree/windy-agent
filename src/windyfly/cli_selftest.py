"""``windy test`` — self-test mode.

Sends a simple message to the agent loop and verifies the full pipeline:
1. Response is returned
2. Response was saved to episodes table
3. Cost was logged to cost_ledger
"""

from __future__ import annotations

import sys
import time
import uuid

from rich.console import Console

console = Console()


def run_self_test() -> None:
    """Run the agent self-test.

    Steps:
        1. Send "What is 2+2?" to the agent loop
        2. Verify a response is returned (non-empty string)
        3. Check the response was saved to episodes table
        4. Check cost was logged to cost_ledger
        5. Print pass/fail summary
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
        sys.exit(1)

    db_path = config.get("memory", {}).get("db_path", "data/windyfly.db")

    try:
        from windyfly.memory.database import Database
        from windyfly.memory.write_queue import WriteQueue

        db = Database(db_path)
        write_queue = WriteQueue()
        write_queue.start()
    except Exception as e:
        console.print(f"  [red]✗ Database/WriteQueue init failed:[/red] {e}")
        sys.exit(1)

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
        sys.exit(1)
