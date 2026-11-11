"""WL-AG1, Windy Agent's codon in the Windy Log strand: the log surface sticks.

A recording holds other people's words. So a model call that carries anything a ``windy_log`` tool returned must tell
Windy Mind it is on the ``log`` surface, and so must every later model call of that conversation, because the answer
stays in the context. Mind then lets only the models that surface allows answer (Windy Mind, 2026-11-11).

Today this agent offers NO ``windy_log`` tool and names no surface to Mind at all, so no model call of its can hold
a Windy Log answer. This file keeps that true in the one way a static test can: the day the words ``windy_log`` or
``log-tools`` appear anywhere in the agent's source, it fails and says what has to arrive in the same PR.

When you add the tool: make every model call whose context holds a ``windy_log`` answer carry the log surface for the
rest of that conversation, prove it with a behaviour test (a turn after the tool call, and a turn after a restart
that reloads the conversation), and replace this tripwire with that test. Do not add the names to an allow-list here.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "windyfly"

# The tool family of log-tools.v1, however it is spelled in code, a contract file or a prompt.
_WINDY_LOG = re.compile(r"(?<![a-z])windy[_\-. ]?log(?![a-z])|(?<![a-z])log[_\-]tools", re.IGNORECASE)
_TEXT_SUFFIXES = {".py", ".json", ".md", ".txt", ".toml", ".yaml", ".yml"}


def _hits() -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*")):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES or "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _WINDY_LOG.search(line):
                found.append(f"{path.relative_to(SRC)}:{number}")
    return found


def test_the_body_offers_no_windy_log_tool_until_the_log_surface_rule_arrives_with_it():
    hits = _hits()
    assert not hits, (
        "Windy Log reached the agent's source without its surface rule (codon WL-AG1):\n  "
        + "\n  ".join(hits[:20])
        + "\nIn the same PR: every model call whose context holds a windy_log answer must carry the log surface "
        "for the rest of that conversation, with a behaviour test; then replace this tripwire with that test."
    )


def test_the_tripwire_sees_every_spelling_of_the_tool_family():
    for text in ("windy_log.search", "windy-log", "WINDY_LOG", "windy log answer", "windylog", "log-tools.v1",
                 "log_tools"):
        assert _WINDY_LOG.search(text), text
    for text in ("windy_login", "catalog-tools", "dialog_tools_x", "windy_logs_dir"):
        assert not _WINDY_LOG.search(text), text
