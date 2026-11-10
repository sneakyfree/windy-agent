"""One birth canal, one certifier (Grant 2026-11-10; ADR-059, ADR-064).

Eternitas issues every birth certificate, at the Windy hatch ceremony. This repo must never grow a second one: no
local certificate file, no certificate command, no PDF writer. The old terminal door made a local preview PDF until
0.7.5; its last remnants (``windy cert``, ``/cert``, the unused ``fpdf2`` dependency) were cut on 2026-11-10. This
tripwire keeps them from coming back.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "windyfly"


def test_no_local_birth_certificate_code():
    hits = [
        f"{path.relative_to(ROOT)}:{n}"
        for path in sorted(SRC.rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "birth_certificate" in line
    ]
    assert not hits, f"a local birth certificate is a second certifier; Eternitas is the only one: {hits}"


def test_no_pdf_writer_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    deps = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        deps.extend(extra)
    assert not [d for d in deps if d.lower().startswith(("fpdf", "reportlab"))], deps


def test_no_cert_command_in_the_cli_or_the_chat_registry():
    import windyfly.commands._legacy as legacy
    from windyfly import cli
    from windyfly.commands import core as core_mod
    from windyfly.commands import registry as registry_mod
    from windyfly.commands.setup import init_all_commands

    assert not hasattr(legacy, "cmd_cert")
    assert not hasattr(cli, "_cmd_cert")
    saved_db, saved_config = core_mod._db, core_mod._config
    try:
        init_all_commands(config={})
        names = set(registry_mod.registry._commands.keys())
    finally:
        core_mod._db, core_mod._config = saved_db, saved_config
    assert not names & {"cert", "certificate"}, names & {"cert", "certificate"}
