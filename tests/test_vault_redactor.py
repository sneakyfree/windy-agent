"""Strand G6.1: Vault lease values are masked BY VALUE wherever text reaches the model, a log,
the audit trail or the episode store."""

from __future__ import annotations

import json
import logging
import urllib.parse

import pytest

from windyfly.vault import redactor

TOKEN = "gh_lease_abc123XYZ789-secret"


@pytest.fixture(autouse=True)
def _clean():
    redactor.clear()
    yield
    redactor.clear()


def test_no_values_registered_is_a_noop():
    assert redactor.redact("nothing to hide") == "nothing to hide"


def test_masks_the_raw_url_encoded_and_json_escaped_forms():
    tok = 'ab"cd/ef+gh=ijkl'
    assert redactor.register(tok)
    for form in (tok, urllib.parse.quote(tok, safe=""), urllib.parse.quote_plus(tok), json.dumps(tok)[1:-1]):
        assert tok not in redactor.redact(f"x {form} y") and redactor.MASK in redactor.redact(f"x {form} y")


def test_too_short_values_are_not_registered():
    assert redactor.register("short") is False
    assert redactor.redact("a short word") == "a short word"


def test_forget_stops_masking():
    redactor.register(TOKEN)
    redactor.forget(TOKEN)
    assert redactor.redact(TOKEN) == TOKEN


def test_contains_flags_a_secret_bearing_response():
    redactor.register(TOKEN)
    assert redactor.contains(f"remote: https://x:{TOKEN}@github.com/a/b") and not redactor.contains("clean")


def test_bounded_memory():
    for i in range(redactor.MAX_VALUES + 20):
        redactor.register(f"value-number-{i:05d}")
    assert redactor.active_count() == redactor.MAX_VALUES


class TestHooks:
    def test_log_lines(self):
        from windyfly.observability.redact import RedactingFilter

        redactor.register(TOKEN)
        rec = logging.LogRecord("t", logging.INFO, "f", 1, "git remote https://u:%s@github.com", (TOKEN,), None)
        RedactingFilter().filter(rec)
        assert TOKEN not in rec.msg and redactor.MASK in rec.msg

    def test_audit_args(self):
        from windyfly.agent.capabilities.audit import _redact

        redactor.register(TOKEN)
        assert TOKEN not in _redact(f'{{"cmd": "echo {TOKEN}"}}')

    def test_tool_results_never_reach_the_model_with_a_lease(self, monkeypatch):
        from windyfly.agent import loop

        redactor.register(TOKEN)
        monkeypatch.setattr(loop, "_dispatch_tool_call_raw", lambda *a, **k: json.dumps({"stdout": f"url https://x:{TOKEN}@h"}))
        out = loop._dispatch_tool_call("shell.exec", {}, None, None, None, Exception)
        assert TOKEN not in out and redactor.MASK in out

    def test_episodes_never_store_a_lease(self, tmp_path):
        from windyfly.memory.database import Database
        from windyfly.memory.episodes import save_episode

        db = Database(str(tmp_path / "t.db"))
        redactor.register(TOKEN)
        eid = save_episode(db, "assistant", f"here it is: {TOKEN}", summary=f"s {TOKEN}")
        row = db.fetchone("SELECT content, summary FROM episodes WHERE id = ?", (eid,))
        assert TOKEN not in row["content"] and TOKEN not in (row["summary"] or "")
        db.close()
