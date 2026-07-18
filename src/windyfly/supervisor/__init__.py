"""Cross-platform supervision — the honey-badger immune system that travels.

Three tiers (see ~/Desktop/Grant's Folder/CROSS_PLATFORM_SUPERVISOR_DESIGN.md):
  1. OS keep-alive adapter (systemd/launchd/Windows) — restart a dead process.
  2. In-process scheduler jobs — periodic self-maintenance, no OS timers.
  3. The GUARDIAN — a tiny cross-platform sidecar that catches the wedge
     case (a hung process that can't heal itself) and restarts it.

'Unify on the guardian model' (Grant, 2026-07-18): every OS runs the same
guardian + in-process jobs; the OS layer only keeps processes alive.
"""
