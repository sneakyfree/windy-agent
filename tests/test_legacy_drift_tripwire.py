"""Freeze the commands/core.py ↔ commands/_legacy.py duplication.

The 2026-07-04 audit found ~22 commands implemented TWICE with zero
shared logic (chat registry closure + Rich CLI function) and already
divergent behavior (`windy doctor` vs `/doctor` check different
things). Full unification is scheduled work; until then this tripwire
stops the bleeding: adding a NEW command to both planes fails the
suite, so the duplicate set can only shrink.

When you unify a command, delete it from KNOWN_DUPLICATES — the test
also fails on stale entries, so the list always reflects reality.
"""

from __future__ import annotations

# The frozen 2026-07-04 duplicate set. SHRINK ONLY.
KNOWN_DUPLICATES = frozenset({
    "budget", "cert", "config", "debug", "doctor", "export", "import",
    "kill", "logs", "mail", "memory", "model", "passport", "phone",
    "ps", "repl", "reset", "restart", "skills", "soul", "update",
    "version",
})


def _current_duplicates() -> set[str]:
    import windyfly.commands._legacy as legacy
    from windyfly.commands import registry as registry_mod
    from windyfly.commands.setup import init_all_commands

    legacy_names = {n[4:] for n in dir(legacy) if n.startswith("cmd_")}
    # init_all_commands registers into the module singleton (idempotent
    # re-registration) but also rewires core's module globals — snapshot
    # and restore them so this test doesn't perturb neighbors.
    from windyfly.commands import core as core_mod
    saved_db, saved_config = core_mod._db, core_mod._config
    try:
        init_all_commands(config={})
        registry_names = set(registry_mod.registry._commands.keys())
    finally:
        core_mod._db, core_mod._config = saved_db, saved_config
    return legacy_names & registry_names


def test_no_new_dual_implementations():
    dupes = _current_duplicates()
    new = dupes - KNOWN_DUPLICATES
    assert not new, (
        f"NEW command(s) implemented in BOTH commands/_legacy.py and the "
        f"registry: {sorted(new)}. Don't grow the duplication — implement "
        f"once in the registry and render it from the CLI."
    )


def test_known_duplicates_list_is_current():
    """Entries here must still be duplicated — when you unify one,
    remove it from KNOWN_DUPLICATES so the list tracks reality."""
    dupes = _current_duplicates()
    stale = KNOWN_DUPLICATES - dupes
    assert not stale, (
        f"KNOWN_DUPLICATES entries no longer duplicated (nice!): "
        f"{sorted(stale)} — remove them from the frozen list."
    )
