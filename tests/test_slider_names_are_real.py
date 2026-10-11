"""Every slider name the gateway and dashboard use is a real slider (control_panel.SLIDER_INFO).

`warmth` (deleted 2026-07-26) lived on in the dashboard presets, the gateway's offline slider
defaults and metadata, and the setup TOML; applying a preset fired a PUT for a slider the brain
rejects. This guard reads the TypeScript sources so a retired name cannot come back unnoticed.
"""

from __future__ import annotations

import re
from pathlib import Path

from windyfly.control_panel import SLIDER_INFO

ROOT = Path(__file__).resolve().parent.parent
SERVER = (ROOT / "gateway/src/server.ts").read_text()
PERSONALITY = (ROOT / "gateway/dashboard/src/pages/Personality.tsx").read_text()
REAL = set(SLIDER_INFO)


def _quoted(block: str) -> set[str]:
    return set(re.findall(r"""["']([a-z_]+)["']""", block))


def test_gateway_offline_slider_defaults_are_real():
    block = re.search(r"const names = \[(.*?)\];", SERVER, re.S)
    assert block, "offline slider defaults list not found in server.ts"
    names = _quoted(block.group(1))
    assert names and names <= REAL, sorted(names - REAL)


def test_gateway_offline_slider_metadata_is_real():
    block = re.search(r"const info[^=]*= \{(.*?)\n        \};", SERVER, re.S)
    assert block, "offline slider metadata not found in server.ts"
    keys = set(re.findall(r"^\s{10}([a-z_]+): \{ label:", block.group(1), re.M))
    assert keys and keys <= REAL, sorted(keys - REAL)


def test_dashboard_presets_and_fallback_order_are_real():
    presets = re.search(r"const PRESETS[^=]*= \{(.*?)\n\}", PERSONALITY, re.S)
    assert presets, "PRESETS not found in Personality.tsx"
    keys = set(re.findall(r"([a-z_]+): \d+", presets.group(1)))
    assert keys and keys <= REAL, sorted(keys - REAL)
    fallback = re.search(r"\? Object\.keys\(sliders\)\s*:\s*\[(.*?)\]", PERSONALITY, re.S)
    assert fallback, "fallback slider order not found in Personality.tsx"
    names = _quoted(fallback.group(1))
    assert names and names <= REAL, sorted(names - REAL)


def test_setup_toml_writes_no_retired_slider():
    assert "warmth = ${p.warmth}" not in SERVER
