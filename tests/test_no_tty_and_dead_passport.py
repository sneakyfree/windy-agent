"""Found by the 0.7.2.1 clean-machine proof from PyPI (2026-09-23).

1. `windy go </dev/null` on an already set-up agent died with an EOFError
   traceback at "Already set up! Launch Windy Fly?". With no terminal, every
   setup prompt now takes its stated default.
2. After `windy deregister`, `windy go --keyless` still said the agent was
   "powered by your agent's Windy passport" / "Config written — Windy Mind
   brain", and plain `windy go` said "Windy Mind (free, keyless) configured".
   A revoked or suspended passport can't reach the brain, so no line may
   claim it does. Suspended is reversible: never suggest --force for it.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from rich.prompt import Confirm, Prompt

from windyfly import prompts
from windyfly import quickstart as qs


class _Args:
    key = None
    keyless = False
    force = False
    byok = False


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(qs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("WINDY_HATCH_VIA_HUB", "0")  # the old terminal path
    for var in ("_WINDYFLY_PASSPORT_DEAD", "_WINDYFLY_FORCE_HATCH",
                "ETERNITAS_PASSPORT", "ETERNITAS_PASSPORT_TOKEN"):
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    for pat in qs.KEY_PATTERNS:
        monkeypatch.delenv(pat["env_var"], raising=False)
    monkeypatch.setattr(qs, "can_run", lambda _tool: True)
    monkeypatch.setattr(qs, "_try_pro_broker", lambda _a: False)
    monkeypatch.setattr(qs, "read_clipboard", lambda: "")
    return tmp_path


@pytest.fixture()
def no_terminal(monkeypatch):
    """stdin at EOF, as with `windy go </dev/null`."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))


# ── 1. no terminal: prompts take their default ────────────────────────

@pytest.mark.parametrize("default", [True, False, "1", "", "Windy Fly"])
def test_ask_returns_the_default_on_eof(default, no_terminal, capsys):
    ask_fn = Confirm.ask if isinstance(default, bool) else Prompt.ask
    assert prompts.ask(ask_fn, "  Question?", default=default) == default
    assert "no terminal to answer" in capsys.readouterr().out


def test_ask_passes_answers_through_when_there_is_a_terminal():
    assert prompts.ask(lambda q, default: "typed", "Q?", default="x") == "typed"


def test_already_set_up_go_without_a_terminal_launches_instead_of_crashing(
    project, no_terminal, capsys
):
    qs.write_keyless_config()
    with patch.object(qs, "_launch") as launch, \
         patch.object(qs, "passport_status", lambda _p, **_k: ""):
        qs.cmd_go(_Args())          # used to raise EOFError here
    launch.assert_called_once()
    out = capsys.readouterr().out
    assert "no terminal to answer" in out and "Traceback" not in out


def test_menu_without_a_terminal_takes_the_free_option(project, no_terminal):
    with patch.object(qs, "_go_keyless") as keyless:
        qs.cmd_go(_Args())
    keyless.assert_called_once()


# ── 2. a dead passport is never shown as working ──────────────────────

def _revoked_env(root, passport="ET26-DEAD-0002"):
    qs.write_keyless_config()
    env = root / ".env"
    text = env.read_text(encoding="utf-8").replace(
        "ETERNITAS_PASSPORT_TOKEN=", f"ETERNITAS_PASSPORT={passport}\n"
        f"# REVOKED 20260923T220005Z (windy deregister, {passport}): "
        "ETERNITAS_PASSPORT_TOKEN=", 1)
    env.write_text(text, encoding="utf-8")


_BRAIN_CLAIMS = ("powered by your agent's Windy passport", "Windy Mind brain,",
                 "Windy Mind (free, keyless) configured", "brain connected")


def test_keyless_go_after_deregister_makes_no_brain_claims(project, capsys):
    _revoked_env(project)
    with patch.object(qs, "_launch"), patch.object(qs, "_install_deps"), \
         patch("windyfly.hatch_orchestrator.run_hatch",
               side_effect=AssertionError("must not hatch")):
        qs._go_keyless(_Args())
    out = capsys.readouterr().out
    assert not [c for c in _BRAIN_CLAIMS if c in out], out
    assert "ET26-DEAD-0002) is revoked" in out and "windy go --force" in out


def test_plain_go_after_deregister_says_revoked(project, monkeypatch, capsys):
    _revoked_env(project)
    monkeypatch.setattr(qs.Confirm, "ask", staticmethod(lambda *a, **k: False))
    qs.cmd_go(_Args())
    out = capsys.readouterr().out
    assert not [c for c in _BRAIN_CLAIMS if c in out], out
    assert "is revoked at Eternitas" in out and "isn't connected" in out


@pytest.mark.parametrize("entry", ["keyless", "plain"])
def test_suspended_is_reversible_so_no_force_and_no_brain_claims(
    entry, project, monkeypatch, capsys
):
    qs.write_keyless_config()
    env = project / ".env"
    env.write_text(env.read_text(encoding="utf-8") + "ETERNITAS_PASSPORT=ET26-HOLD-0002\n",
                   encoding="utf-8")
    monkeypatch.setattr(qs, "passport_status", lambda _p, **_k: "suspended")
    monkeypatch.setattr(qs.Confirm, "ask", staticmethod(lambda *a, **k: False))
    with patch.object(qs, "_launch"), patch.object(qs, "_install_deps"), \
         patch("windyfly.hatch_orchestrator.run_hatch",
               side_effect=AssertionError("must not hatch")):
        if entry == "keyless":
            qs._go_keyless(_Args())
        else:
            qs.cmd_go(_Args())
    out = capsys.readouterr().out
    assert "ET26-HOLD-0002 is suspended at Eternitas (reversible)" in out
    assert "--force" not in out
    assert not [c for c in _BRAIN_CLAIMS if c in out], out


def test_live_passport_keeps_the_normal_wording(project, monkeypatch, capsys):
    qs.write_keyless_config()
    monkeypatch.setattr(qs, "passport_status", lambda _p, **_k: "")
    monkeypatch.setattr(qs.Confirm, "ask", staticmethod(lambda *a, **k: False))
    qs.cmd_go(_Args())
    assert "Windy Mind (free, keyless) configured" in capsys.readouterr().out
