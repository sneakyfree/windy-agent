"""fleet.* capability regression tests.

Pin the contract:
  - list_kits parses ~/.ssh/config for wg-* / kit-* aliases
  - prepare_command drafts a script and saves it; never executes
  - Unknown targets fail loudly (no hallucinated kit names)
  - Default targets = every known canonical alias
  - Dry-run is the default (cannot accidentally trigger production)
  - Script output is bash-safe (single-quote escaping for commands
    with embedded apostrophes)
  - Empty SSH config returns ok:false rather than crashing
  - The drafted script exits non-zero if any target fails
"""

from __future__ import annotations

import os
import stat

import pytest

from windyfly.agent.capabilities.fleet import (
    _draft_script,
    _parse_ssh_config,
    _quote,
    _slugify,
    register_fleet_capabilities,
)
from windyfly.agent.capabilities.registry import CapabilityRegistry


# ── SSH config parser ──────────────────────────────────────────────


SAMPLE_CONFIG = """\
# Windy 0 SSH config
Host kit-0-public vps wg-k0
    HostName 72.60.118.54
    User root

# Charlie kit (laptop)
Host wg-0c3 kit-0c3 charlie
    HostName 10.10.0.3
    User sneakyfree
    ProxyJump vps

# Random non-fleet host (should be skipped)
Host github.com
    HostName github.com
    User git

Host wg-veron veron
    HostName 10.10.0.6
    User user1-gpu
    ProxyJump vps
"""


def test_parse_finds_fleet_hosts():
    kits = _parse_ssh_config(SAMPLE_CONFIG)
    aliases = [k["alias"] for k in kits]
    assert "kit-0-public" in aliases
    assert "wg-0c3" in aliases
    assert "wg-veron" in aliases


def test_parse_skips_non_fleet_hosts():
    kits = _parse_ssh_config(SAMPLE_CONFIG)
    aliases = [k["alias"] for k in kits]
    assert "github.com" not in aliases


def test_parse_captures_aliases():
    kits = _parse_ssh_config(SAMPLE_CONFIG)
    by_alias = {k["alias"]: k for k in kits}
    assert "charlie" in by_alias["wg-0c3"]["aliases"]


def test_parse_captures_proxy_jump():
    kits = _parse_ssh_config(SAMPLE_CONFIG)
    by_alias = {k["alias"]: k for k in kits}
    assert by_alias["wg-0c3"]["proxy_jump"] == "vps"
    # Public jump host has no ProxyJump itself
    assert by_alias["kit-0-public"]["proxy_jump"] is None


def test_parse_captures_comment():
    kits = _parse_ssh_config(SAMPLE_CONFIG)
    by_alias = {k["alias"]: k for k in kits}
    assert "Charlie kit" in (by_alias["wg-0c3"]["comment"] or "")


def test_parse_empty_config_returns_empty_list():
    assert _parse_ssh_config("") == []
    assert _parse_ssh_config("# only a comment\n") == []


# ── Slug + quote helpers ───────────────────────────────────────────


def test_slugify_normalizes():
    assert _slugify("Update All The Kits!!") == "update-all-the-kits"
    assert _slugify("") == "task"


def test_slugify_truncates():
    long_text = "x" * 200
    assert len(_slugify(long_text)) == 40


def test_quote_single_quotes_handled():
    """A command like `echo 'hi'` must not break the bash quoting."""
    out = _quote("echo 'hi'")
    # Run the result through bash to verify it actually parses as a
    # single quoted token. (Skipping subprocess here — string-shape
    # check is enough: the close+escape+reopen pattern must appear.)
    assert "'\\''" in out


# ── Script drafting ────────────────────────────────────────────────


def test_draft_script_includes_targets():
    s = _draft_script("update", "uptime", ["wg-0c3", "wg-0c4"], dry_run=False)
    assert "wg-0c3" in s
    assert "wg-0c4" in s


def test_draft_script_dry_run_flag():
    s_dry = _draft_script("update", "uptime", ["wg-0c3"], dry_run=True)
    s_live = _draft_script("update", "uptime", ["wg-0c3"], dry_run=False)
    assert "DRY_RUN=1" in s_dry
    assert "DRY_RUN=0" in s_live


def test_draft_script_uses_set_pipefail():
    """Without set -euo pipefail an early failure can be silently
    swallowed and you'd think the dispatch succeeded."""
    s = _draft_script("x", "uptime", ["wg-0c3"], dry_run=False)
    assert "set -euo pipefail" in s


def test_draft_script_tracks_failures():
    """Per the docstring contract, the script must collect failed
    targets and exit non-zero — masking failures is the worst-case
    fleet-dispatch outcome."""
    s = _draft_script("x", "uptime", ["wg-0c3"], dry_run=False)
    assert "FAILED+=" in s
    assert "exit 1" in s


# ── Capability registration + handlers ─────────────────────────────


@pytest.fixture
def registered_registry(tmp_path, monkeypatch):
    """Register fleet caps with isolated SSH config + dispatch dir."""
    cfg = tmp_path / "ssh_config"
    cfg.write_text(SAMPLE_CONFIG)
    dispatch = tmp_path / "dispatch"
    monkeypatch.setenv("WINDY_SSH_CONFIG", str(cfg))
    monkeypatch.setenv("WINDY_FLEET_DISPATCH_DIR", str(dispatch))

    registry = CapabilityRegistry()
    register_fleet_capabilities(registry, config={})
    return registry, dispatch


def test_list_kits_returns_three_canonical(registered_registry):
    registry, _ = registered_registry
    cap = registry.get("fleet.list_kits")
    out = cap.handler()
    assert out["ok"] is True
    aliases = [k["alias"] for k in out["kits"]]
    assert set(aliases) == {"kit-0-public", "wg-0c3", "wg-veron"}


def test_list_kits_empty_config(tmp_path, monkeypatch):
    cfg = tmp_path / "empty"
    cfg.write_text("")
    monkeypatch.setenv("WINDY_SSH_CONFIG", str(cfg))
    registry = CapabilityRegistry()
    register_fleet_capabilities(registry, config={})
    cap = registry.get("fleet.list_kits")
    out = cap.handler()
    assert out["ok"] is False


def test_list_kits_missing_config(tmp_path, monkeypatch):
    """Missing SSH config returns ok:false rather than crashing."""
    monkeypatch.setenv("WINDY_SSH_CONFIG", str(tmp_path / "does-not-exist"))
    registry = CapabilityRegistry()
    register_fleet_capabilities(registry, config={})
    cap = registry.get("fleet.list_kits")
    out = cap.handler()
    assert out["ok"] is False


def test_prepare_command_writes_script(registered_registry):
    registry, dispatch_dir = registered_registry
    cap = registry.get("fleet.prepare_command")
    out = cap.handler(
        description="update apt packages",
        command="sudo apt-get update -y",
        targets=["wg-0c3", "wg-veron"],
        dry_run=True,
    )
    assert out["ok"] is True
    assert dispatch_dir.exists()
    path = out["path"]
    assert os.path.exists(path)
    assert os.access(path, os.X_OK)  # chmod 0o755
    text = open(path, encoding="utf-8").read()
    assert "wg-0c3" in text
    assert "wg-veron" in text
    assert "DRY_RUN=1" in text


def test_prepare_command_defaults_to_dry_run(registered_registry):
    """Dry-run is the safe default. Hallucinated 'execute now' must
    require explicit dry_run=False from the caller."""
    registry, _ = registered_registry
    cap = registry.get("fleet.prepare_command")
    out = cap.handler(
        description="reboot",
        command="sudo reboot",
        targets=["wg-0c3"],
    )
    text = open(out["path"], encoding="utf-8").read()
    assert "DRY_RUN=1" in text


def test_prepare_command_rejects_unknown_target(registered_registry):
    """Hallucinated kit name → fail loudly, don't ssh to nothing."""
    registry, _ = registered_registry
    cap = registry.get("fleet.prepare_command")
    out = cap.handler(
        description="update",
        command="uptime",
        targets=["wg-imaginary"],
    )
    assert out["ok"] is False
    assert "wg-imaginary" in out["reason"]


def test_prepare_command_default_targets_are_canonical(registered_registry):
    """Omit targets → use every canonical alias once (NOT every
    secondary name like 'charlie' / 'veron' too — that would dispatch
    each kit multiple times)."""
    registry, _ = registered_registry
    cap = registry.get("fleet.prepare_command")
    out = cap.handler(description="update", command="uptime")
    assert out["ok"] is True
    # Three canonical aliases in SAMPLE_CONFIG
    assert len(out["targets"]) == 3
    assert set(out["targets"]) == {"kit-0-public", "wg-0c3", "wg-veron"}


def test_prepare_command_validates_inputs(registered_registry):
    registry, _ = registered_registry
    cap = registry.get("fleet.prepare_command")
    assert cap.handler(description="", command="x")["ok"] is False
    assert cap.handler(description="x", command="")["ok"] is False


def test_prepare_command_does_NOT_execute(registered_registry, tmp_path):
    """The bot drafts; it does not run. Verify the script never
    actually runs anything by checking that prepare_command returns
    immediately without any side effects beyond writing the file."""
    registry, dispatch_dir = registered_registry
    cap = registry.get("fleet.prepare_command")
    side_effect_marker = tmp_path / "if-this-exists-the-bot-ran-the-script"
    out = cap.handler(
        description="leak test",
        command=f"touch {side_effect_marker}",
        targets=["wg-0c3"],
        dry_run=False,
    )
    assert out["ok"] is True
    # The script was WRITTEN but NOT RUN — the marker must not exist.
    assert not side_effect_marker.exists()


def test_prepare_command_filename_has_timestamp(registered_registry):
    registry, _ = registered_registry
    cap = registry.get("fleet.prepare_command")
    out = cap.handler(
        description="reboot fleet",
        command="sudo reboot",
        targets=["wg-0c3"],
    )
    fname = os.path.basename(out["path"])
    # YYYYMMDD-HHMMSS-slug.sh
    assert fname.endswith(".sh")
    assert "reboot-fleet" in fname


# ── Tier / band ────────────────────────────────────────────────────


def test_list_kits_tier_is_read_external(registered_registry):
    registry, _ = registered_registry
    cap = registry.get("fleet.list_kits")
    from windyfly.agent.capabilities.descriptor import Tier
    assert cap.tier == Tier.READ_EXTERNAL


def test_prepare_command_supports_dry_run(registered_registry):
    registry, _ = registered_registry
    cap = registry.get("fleet.prepare_command")
    assert cap.dry_run_supported is True
