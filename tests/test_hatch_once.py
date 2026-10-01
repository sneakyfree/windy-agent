"""One agent, one passport — config rewrites keep the identity.

Found by the read-only hatch audit (2026-09-23): re-running `windy go`
rewrote .env from scratch (blanking the EPT too), so the next hatch minted a
SECOND passport for the same agent. The config writers must keep it.

(The terminal-hatch cases that used to live here went with the terminal
hatch in 0.7.5 — ADR-059, one hallway.)
"""

from __future__ import annotations

import pytest

from windyfly import quickstart


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = quickstart.PROJECT_ROOT  # conftest's per-test project root
    monkeypatch.setenv("WINDYFLY_HOME", str(root))
    monkeypatch.delenv("WINDY_ENV_FILE", raising=False)
    for var in ("ETERNITAS_PASSPORT", "ETERNITAS_PASSPORT_TOKEN", "WINDY_JWT",
                "_WINDYFLY_FORCE_HATCH", "_WINDYFLY_PASSPORT_DEAD"):
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    return root


def _env(root) -> dict[str, str]:
    return quickstart._read_env_values(root / ".env")


def test_rewriting_config_keeps_the_identity(home):
    (home / ".env").write_text(
        "ETERNITAS_PASSPORT=ET26-KEEP-0001\nETERNITAS_PASSPORT_TOKEN=jwt.keep.sig\n"
    )
    quickstart.write_keyless_config()
    env = _env(home)
    assert env["ETERNITAS_PASSPORT"] == "ET26-KEEP-0001"
    assert env["ETERNITAS_PASSPORT_TOKEN"] == "jwt.keep.sig"


def test_env_file_is_private(home):
    """The .env holds the passport token: owner-only, even if it existed 0644."""
    env = home / ".env"
    env.write_text("OLD=1\n")
    env.chmod(0o644)
    quickstart._write_env_keeping_identity(["ETERNITAS_PASSPORT_TOKEN=eyJ.x.y"])
    assert env.stat().st_mode & 0o777 == 0o600


def test_status_table_never_asks_a_customer_for_a_server_secret(home, monkeypatch):
    import io

    from rich.console import Console

    from windyfly import hatching

    for var in ("MATRIX_BOT_TOKEN", "MATRIX_BOT_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    buf = io.StringIO()
    monkeypatch.setattr(hatching, "console", Console(file=buf, width=160, color_system=None))
    hatching.show_ecosystem_status(None, None)
    assert "SYNAPSE_REGISTRATION_SECRET" not in buf.getvalue()
