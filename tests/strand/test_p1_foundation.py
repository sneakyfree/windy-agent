"""Strand v2, Part 1: §1 identity, §2 architecture, §3 repo layout, gene 0.1 repo setup and gene
0.2 config (docs/strand/STRAND_V2.md). Repo files are read from the checkout; no network, no home.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def tracked(path: str) -> bool:
    out = subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ROOT,
                         capture_output=True, text=True)
    return out.returncode == 0


# ── §1 identity ───────────────────────────────────────────────────────────────────────

def test_p1_1_name_and_tagline():
    from windyfly import branding

    assert branding.IS_WINDY_FLY
    assert branding.BRAND_NAME == "Windy Fly"
    assert branding.BRAND_TAGLINE == "Your AI. Your Rules. Your Ecosystem."


def test_p1_1_part_of_the_windy_ecosystem():
    from windyfly import branding

    assert branding.HAS_ECOSYSTEM and branding.HAS_MATRIX_AUTOPROVISION
    assert branding.BRAND_HOMESERVER == "https://chat.windychat.ai"


NOTICE = "Copyright 2026 Windstorm Labs LLC, all rights reserved."


def test_p1_1_licence_is_proprietary():
    assert tracked("LICENSE") and (ROOT / "LICENSE").read_text(encoding="utf-8").strip() == NOTICE
    assert NOTICE in PYPROJECT["project"]["license"]["text"]
    assert "License :: Other/Proprietary License" in PYPROJECT["project"]["classifiers"]
    assert not [c for c in PYPROJECT["project"]["classifiers"]
                if c.startswith("License ::") and "Proprietary" not in c]
    assert NOTICE in (ROOT / "README.md").read_text(encoding="utf-8")       # the PyPI page


def test_p1_1_python_312_or_newer():
    spec = PYPROJECT["project"]["requires-python"]
    assert re.fullmatch(r">=\s*3\.(1[2-9]|[2-9]\d)", spec), spec


def test_p1_1_bun_gateway_is_tested_in_ci():
    pkg = (ROOT / "gateway" / "package.json").read_text(encoding="utf-8")
    assert '"test"' in pkg
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "gateway-test" in ci and "bun test" in ci


def test_p1_1_matrix_nio_is_the_chat_protocol():
    deps = " ".join(PYPROJECT["project"]["dependencies"])
    assert "matrix-nio" in deps
    import nio  # noqa: F401  (importable in the installed env)


def test_p1_1_locked_with_uv_and_bun():
    assert tracked("uv.lock") and tracked("gateway/bun.lock")


# ── §3 layout / gene 0.1 ──────────────────────────────────────────────────────────────

LAYOUT = {
    "root": ["pyproject.toml", "windyfly.toml", "SOUL.md", "README.md", ".gitignore", ".env.example"],
    "core": ["src/windyfly/__init__.py", "src/windyfly/main.py", "src/windyfly/config.py"],
    "agent": ["src/windyfly/agent/loop.py", "src/windyfly/agent/prompt.py", "src/windyfly/agent/models.py"],
    "memory": [f"src/windyfly/memory/{m}.py" for m in
               ("database", "episodes", "nodes", "soul", "skills", "cost_ledger", "failures", "write_queue")],
    "personality": ["src/windyfly/personality/engine.py"],
    "channels": ["src/windyfly/channels/matrix_bot.py", "src/windyfly/channels/cli.py"],
    "tools": ["src/windyfly/tools/registry.py", "src/windyfly/tools/windy_api.py", "src/windyfly/tools/web_search.py"],
    "tests": ["tests/__init__.py", "tests/test_memory.py", "tests/test_personality.py"],
    "data": ["data/.gitkeep"],
}


@pytest.mark.parametrize("group", sorted(LAYOUT))
def test_p1_3_layout(group):
    missing = [p for p in LAYOUT[group] if not tracked(p)]
    assert not missing, missing


def test_p1_01_gitignore_keeps_secrets_and_state_out():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    for pattern in (".env", "data/*.db", "data/*.db-wal", "data/providers.json", "data/provider_keys.json",
                    "__pycache__/", ".venv/", "node_modules/"):
        assert pattern in ignore, pattern


def test_p1_01_env_example_names_each_key_once():
    names = re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(encoding="utf-8"))
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, dupes
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MATRIX_HOMESERVER", "ETERNITAS_URL", "LOG_LEVEL"):
        assert key in names


def test_p1_01_package_named_windyfly_with_its_runtime_deps():
    assert PYPROJECT["project"]["name"] == "windyfly"
    deps = " ".join(PYPROJECT["project"]["dependencies"])
    for dep in ("httpx", "matrix-nio", "python-dotenv"):
        assert dep in deps, dep


# ── gene 0.2 config ───────────────────────────────────────────────────────────────────

def _load(tmp_path, monkeypatch, text: str) -> dict:
    from windyfly.config import load_config

    for k in ("MATRIX_HOMESERVER", "DEFAULT_MODEL", "WINDYFLY_DB_PATH"):
        monkeypatch.delenv(k, raising=False)
    path = tmp_path / "windyfly.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(str(path))


def test_p1_02_there_is_one_default_model():
    from windyfly.config import DEFAULT_CONFIG, DEFAULT_MODEL, default_model

    toml = tomllib.loads((ROOT / "windyfly.toml").read_text(encoding="utf-8"))
    assert toml["agent"]["default_model"] == DEFAULT_MODEL == DEFAULT_CONFIG["agent"]["default_model"]
    assert not re.search(r"(?m)^DEFAULT_MODEL=", (ROOT / ".env.example").read_text(encoding="utf-8"))
    assert default_model({}) == DEFAULT_MODEL
    assert default_model({"agent": {"default_model": "x-model"}}) == "x-model"
    # The agent's own setting read with a fallback of its own (a per-provider default is another thing).
    own = re.compile(r"""(?:["']agent["'][^\n]*?\)|\bagent\w*)\s*\.get\(\s*["']default_model["']\s*(?:,|\)\s*or\s*["'])""")
    hits = [str(p.relative_to(ROOT)) for p in (ROOT / "src").rglob("*.py") if own.search(p.read_text(encoding="utf-8"))]
    assert not hits, f"these name their own fallback model: {hits}"


def test_p1_02_memory_settings_come_from_the_file(tmp_path, monkeypatch):
    cfg = _load(tmp_path, monkeypatch, '[memory]\ndb_path = "data/x.db"\nmax_nodes_per_context = 3\n')
    assert cfg["memory"]["db_path"] == "data/x.db"
    assert cfg["memory"]["max_nodes_per_context"] == 3


def test_p1_02_personality_sliders_default_from_the_file(tmp_path, monkeypatch):
    from windyfly.control_panel import get_sliders
    from windyfly.memory.database import Database

    cfg = _load(tmp_path, monkeypatch, "[personality]\nhumor = 2\nformality = 9\n")
    db = Database(":memory:")
    try:
        sliders = get_sliders(db, config_defaults=cfg["personality"])
    finally:
        db.close()
    assert sliders["humor"] == 2 and sliders["formality"] == 9


def test_p1_02_matrix_settings_come_from_the_file(tmp_path, monkeypatch):
    cfg = _load(tmp_path, monkeypatch, '[matrix]\nhomeserver = "https://chat.example.test"\n')
    assert cfg["matrix"]["homeserver"] == "https://chat.example.test"
