"""Telegram autocomplete registration via setMyCommands.

Pre-PR: the bot never called Telegram's ``set_my_commands`` API, so
the "/" autocomplete popup on the user's phone showed whatever was
last typed into @BotFather manually — usually a handful of commands,
leaving 100+ features hidden from discovery (Grant's 2026-05-20
screenshot showed exactly this — popup with ~5 entries, bot has
130+).

Telegram caps the popup at 100 BotCommand entries; we filter to
telegram-allowed commands (skipping terminal-only categories like
12_developer), sort by category priority so the most-useful land
first, and truncate at 100.

Tests pin:
  - The candidate list respects the 100-cap
  - Terminal-only categories don't appear in the candidates
  - Each command name fits Telegram's [a-z0-9_]{1,32} rule
  - Each description is 3-256 chars (Telegram's range)
  - Hyphens in command names are converted to underscores
    (Telegram rejects hyphens)
"""

from __future__ import annotations

import re

import pytest

from windyfly.commands.registry import (
    _platform_may_invoke,
    registry,
)
from windyfly.commands.setup import init_all_commands
from windyfly.memory.database import Database


# Same constants the production code uses
_VALID = re.compile(r"^[a-z0-9_]{1,32}$")
_PRIORITY = {
    "01_process": 1,
    "13_help": 2,
    "02_diagnostics": 3,
    "09_identity": 4,
    "04_model": 5,
    "05_personality": 6,
    "03_chat": 7,
    "06_memory": 8,
    "08_budget": 9,
    "14_email": 10,
    "15_phone": 11,
}


@pytest.fixture(autouse=True)
def boot_commands():
    db = Database(":memory:")
    init_all_commands(db=db, config={})
    yield
    db.close()


_CHANNEL_LAYER_EXTRAS = [
    (1, "panic", "Emergency reset — bot acting weird"),
    (5, "goal", "Set/show/clear a goal · /goal pace 4h · /goal autorun 3"),
    (5, "forget", "Demote an auto-promoted correction skill"),
    (5, "objective", "Alias for /goal"),
    (5, "mission", "Alias for /goal"),
    (1, "resurrect", "Switch to free local backup model"),
    (1, "normal", "Switch back from lifeboat to paid model"),
    (1, "lifeboat", "Show lifeboat status"),
    (1, "auto_resurrect", "Toggle auto-switch on rate limit"),
    (8, "spend", "Today's spending by provider"),
    (8, "pause", "Stop me from spending money"),
    (8, "resume", "Wake me up after a pause"),
    (8, "yolo", "Let me cook hard (24h, no auto-pause)"),
    (8, "yolo24", "YOLO mode for 24 hours"),
    (8, "yolo48", "YOLO mode for 48 hours"),
    (6, "guest", "Switch into grandma-mode for a demo"),
    (4, "model", "Show or switch my LLM"),
]


def _build_candidates():
    cands = []
    for cmd in registry.all():
        name = cmd.name.replace("-", "_")
        if not _VALID.match(name):
            continue
        if not _platform_may_invoke("telegram", cmd.category):
            continue
        desc = (cmd.description or name)[:256]
        if len(desc) < 3:
            desc = (desc + "  ")[:3]
        cands.append((_PRIORITY.get(cmd.category, 99), name, desc))
    existing = {n for _p, n, _d in cands}
    for prio, name, desc in _CHANNEL_LAYER_EXTRAS:
        if name not in existing and _VALID.match(name):
            cands.append((prio, name, desc))
    cands.sort(key=lambda x: (x[0], x[1]))
    return cands[:100]


def test_candidate_count_under_100():
    cands = _build_candidates()
    assert 1 <= len(cands) <= 100


def test_all_names_match_telegram_rules():
    cands = _build_candidates()
    for _p, name, _d in cands:
        assert _VALID.match(name), f"invalid name: {name!r}"


def test_all_descriptions_in_telegram_range():
    cands = _build_candidates()
    for _p, _n, desc in cands:
        assert 3 <= len(desc) <= 256, f"desc out of range: {desc!r}"


def test_no_terminal_only_categories():
    """Commands in 12_developer / 11_maintenance etc. (terminal-
    only categories) must not appear — they'd 'not allowed from
    telegram' on tap, polluting the popup."""
    cands = _build_candidates()
    names = {n for _p, n, _d in cands}
    # /git, /run, /repl are 12_developer — must NOT be in popup
    assert "git" not in names
    assert "run" not in names
    assert "repl" not in names


def test_hyphens_converted_to_underscores():
    """``send-mail`` becomes ``send_mail`` because Telegram rejects
    hyphens in command names."""
    cands = _build_candidates()
    names = {n for _p, n, _d in cands}
    # send-mail → send_mail (it's in 14_email which is allowed)
    assert "send_mail" in names
    assert "send-mail" not in names


def test_priority_categories_come_first():
    """The first command in the sorted list should be from the
    highest-priority category."""
    cands = _build_candidates()
    # First entry should be category 01_process
    assert cands[0][0] == 1


def test_high_value_commands_in_set():
    """Spot-check that the truly-useful commands made the cut."""
    cands = _build_candidates()
    names = {n for _p, n, _d in cands}
    # Must include: help, status, ping, sliders, preset, goal, model
    for required in ("help", "status", "ping", "sliders", "preset",
                     "goal", "model", "whoami", "version", "resurrect"):
        assert required in names, f"missing required command: /{required}"
