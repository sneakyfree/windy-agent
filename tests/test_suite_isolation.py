"""Guards for the suite-wide isolation fixtures (2026-07-04 audit).

Two production incidents motivated the autouse fixtures these tests pin:

1. Running the suite on the prod box killed the live windy-0.service —
   ``test_edge_cases`` reached ``cli._do_kill_by_name`` → real
   ``pkill -f windyfly.main``.
2. Running the suite on 0c2 overwrote the repo's real ``windyfly.toml``
   and ``.env`` — ``test_pro_broker`` ran ``quickstart.cmd_go`` without
   patching ``quickstart.PROJECT_ROOT``.

If either fixture is removed or stops covering a module, these fail.
"""

from __future__ import annotations

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_project_root_is_isolated_by_default():
    import windyfly.cli as cli
    import windyfly.commands._legacy as legacy
    import windyfly.commands.core as core
    import windyfly.quickstart as quickstart
    import windyfly.setup_wizard as setup_wizard

    for mod in (cli, quickstart, setup_wizard, core, legacy):
        assert Path(mod.PROJECT_ROOT) != REPO_ROOT, (
            f"{mod.__name__}.PROJECT_ROOT points at the real repo during "
            "a test — the autouse _isolate_project_root fixture is broken"
        )
    assert setup_wizard.CONFIG_FILE != REPO_ROOT / "windyfly.toml"
    assert setup_wizard.ENV_FILE != REPO_ROOT / ".env"


def test_kill_by_name_is_neutered_by_default():
    """cmd_stop's pkill fall-through must be a no-op unless a test
    opts in with the real_process_kill marker."""
    import windyfly.cli as cli
    from unittest.mock import MagicMock

    assert isinstance(cli.kill_by_name, MagicMock), (
        "cli.kill_by_name is the real function during a test — the "
        "autouse _no_real_process_kills fixture is broken"
    )
    # And the full cmd_stop path must be safe to invoke.
    cli.cmd_stop(argparse.Namespace())
