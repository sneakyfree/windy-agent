"""The telemetry disclosure, and the consent it records.

Rule (orchestrator, for every public client): a user's machine sends
nothing until the user has been told, in one plain line, what is sent.
Showing the line writes a marker in the Windy state dir, and the
customer send paths (client token, the agent's own passport token) only
open once that marker exists. The fleet's own emitter token is exempt.

The line is shown:
- at the end of a successful hatch (the terminal ceremony, and the hub
  ticket's "It's alive!"), which is when an agent first gets a passport;
- at ``windy login`` / ``windy go``, if a send path already exists;
- by ``windy telemetry status`` / ``windy telemetry on``.

``WINDY_TELEMETRY=1`` set explicitly counts as informed consent (for a
headless install that never runs an interactive command).
``WINDY_TELEMETRY=0`` or ``windy telemetry off`` turns everything off.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

LINE = (
    "Windy Fly sends anonymous health data (codes, counts, durations, never "
    "your messages). Turn off: WINDY_TELEMETRY=0 or `windy telemetry off`"
)
_MARKER = "telemetry_disclosed"
_PREF = "telemetry_pref"


def _state(name: str) -> Path:
    from windyfly.platform import windy_state_dir

    return windy_state_dir() / name


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


# ── preference + consent ─────────────────────────────────────────────

def preference() -> str | None:
    """"on", "off", or None (never set). The env var wins over the file."""
    env = os.environ.get("WINDY_TELEMETRY", "").strip()
    if env == "0":
        return "off"
    if env == "1":
        return "on"
    try:
        value = _state(_PREF).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if value in ("on", "off") else None


def set_preference(value: str) -> None:
    if value not in ("on", "off"):
        raise ValueError(value)
    _write_private(_state(_PREF), value + "\n")


def opted_out() -> bool:
    return preference() == "off"


def disclosed() -> bool:
    try:
        return _state(_MARKER).exists()
    except OSError:
        return False


def consented() -> bool:
    """The user has been told (marker) or said yes explicitly
    (WINDY_TELEMETRY=1 / ``windy telemetry on``), and hasn't opted out."""
    if opted_out():
        return False
    return disclosed() or preference() == "on"


# ── showing the line ─────────────────────────────────────────────────

def _show_once(show: Callable[[str], object], *, force: bool = False) -> bool:
    if opted_out():
        return False
    if disclosed() and not force:
        return False
    show(LINE)
    try:
        _write_private(_state(_MARKER), "1\n")
    except OSError:
        pass  # showing it again next time is the safe failure
    return True


def maybe_show(show: Callable[[str], object]) -> bool:
    """At ``windy login`` / ``windy go``: once, and only if this machine
    has a way to send (an agent with a passport, a client token)."""
    from windyfly.observability import admin_telemetry

    if admin_telemetry._ingest_target(require_consent=False) is None:
        return False
    return _show_once(show)


def after_hatch(show: Callable[[str], object]) -> bool:
    """At the end of a successful hatch: the agent now has a passport and
    would start sending, so tell the human first (once)."""
    return _show_once(show)


def show_now(show: Callable[[str], object]) -> bool:
    """``windy telemetry status|on``: always print the line and record it."""
    return _show_once(show, force=True)
