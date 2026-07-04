"""Portability tripwire: no personal/dev-box paths may ship in src/.

The 2026-07-04 architecture audit found the entire recovery layer
(lifeboat, pause, guest, panic flags, failure counters) defaulted to
``/home/grantwhitmer/...`` — so on any machine but the dev box every
flag write failed silently and the "tank" recovery system effectively
did not exist for real users.

These tests (a) fail the suite if a personal absolute path ever lands
in ``src/`` again, and (b) pin the ``windy_state_dir()`` contract that
all flag defaults now derive from.
"""

from __future__ import annotations

from pathlib import Path

from windyfly.platform import windy_state_dir

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"

FORBIDDEN_FRAGMENTS = (
    "/home/grantwhitmer",
    "/Users/thewindstorm",
)


def test_no_personal_paths_in_src():
    offenders: list[str] = []
    for py in SRC_ROOT.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for fragment in FORBIDDEN_FRAGMENTS:
            if fragment in text:
                offenders.append(f"{py.relative_to(SRC_ROOT)}: {fragment}")
    assert not offenders, (
        "Personal dev-box paths found in src/ — use "
        "windyfly.platform.windy_state_dir() (or an env override) instead:\n"
        + "\n".join(offenders)
    )


def test_windy_state_dir_defaults_to_user_home(monkeypatch):
    monkeypatch.delenv("WINDY_STATE_DIR", raising=False)
    assert windy_state_dir() == Path.home() / ".windy"


def test_windy_state_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    assert windy_state_dir() == tmp_path / "state"


def test_recovery_flags_derive_from_state_dir(monkeypatch, tmp_path):
    """Flag paths must follow WINDY_STATE_DIR when their own per-flag
    env override is absent (the per-flag overrides are redirected to
    tmp by conftest, so clear them here to exercise the defaults)."""
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
    per_flag_envs = {
        "WINDY_RESURRECT_FLAG": None,
        "WINDY_PAUSE_FLAG": None,
        "WINDY_YOLO_FLAG": None,
        "WINDY_GUEST_FLAG": None,
        "WINDY_AUTO_RESURRECT_DISABLED": None,
        "WINDY_AUTO_RESURRECT_LAST": None,
        "WINDY_POST_RECOVERY_GRACE": None,
        "WINDY_RECOVERY_PROBE_LAST": None,
        "WINDY_OLLAMA_FAILURE_COUNTER": None,
    }
    for env in per_flag_envs:
        monkeypatch.delenv(env, raising=False)

    from windyfly.agent import guest_mode, resurrect, spend_monitor

    assert resurrect._flag_path() == tmp_path / ".resurrected"
    assert spend_monitor._pause_flag_path() == tmp_path / ".paused"
    assert spend_monitor._yolo_flag_path() == tmp_path / ".yolo"
    assert guest_mode._guest_flag_path() == tmp_path / ".guest"
