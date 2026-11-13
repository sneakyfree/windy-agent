"""Strand v2, new genes N1-N3 (Windy Mind, Eternitas, teams): the promises no other test proved
(docs/strand/STRAND_V2.md, Wave 5g1). No network: every outbound call is stubbed."""

from __future__ import annotations

from unittest.mock import MagicMock

from windyfly.agent import mind_auth, mind_helper, models, teams


def _resp(status: int, error: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.headers = {"x-mind-error": error} if error else {}
    r.json.return_value = {}
    r.text = "{}"
    return r


# ── N1 Windy Mind ────────────────────────────────────────────────────────────────────────

def test_n1_the_mind_helper_is_off_unless_switched_on(monkeypatch):
    monkeypatch.delenv("WINDY_MIND_HELPER", raising=False)
    assert mind_helper.enabled() is False
    monkeypatch.setenv("WINDY_MIND_HELPER", "1")
    assert mind_helper.enabled() is True


def test_n1_a_refused_read_retries_once_with_the_long_lived_token(monkeypatch):
    monkeypatch.setattr(mind_auth, "headers", lambda m, u, e: ({"Authorization": "Bearer agent-tok"}, True))
    monkeypatch.setattr(mind_auth, "forget_token", lambda: None)
    sent: list[dict] = []

    def fake_get(url, headers, timeout):
        sent.append(headers)
        return _resp(401, "expired") if len(sent) == 1 else _resp(200)

    monkeypatch.setattr("httpx.get", fake_get)
    resp = mind_auth.get("https://api.windymind.ai/v1/grants/me", "legacy-ept", 5.0)
    assert resp.status_code == 200
    assert sent == [{"Authorization": "Bearer agent-tok"}, {"Authorization": "Bearer legacy-ept"}]


def test_n1_a_refused_picture_does_not_cool_mind_down(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-test")
    monkeypatch.setattr(models, "_provider_cooldowns", {})
    monkeypatch.setattr(models, "_mind_helper_enabled", lambda: False)
    monkeypatch.setattr("httpx.post", lambda *a, **k: _resp(403, "vision_consent_required"))
    out = models._try_mind_broker([{"role": "user", "content": "x"}], None, None, 100, None)
    assert out is None and models._last_mind_failure == "mind vision_consent_required"
    assert not models._is_provider_in_cooldown("windy-mind")


def test_n1_a_refused_own_key_named_in_x_mind_connection_does_not_cool_mind_down(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-test")
    monkeypatch.setattr(models, "_provider_cooldowns", {})
    monkeypatch.setattr(models, "_mind_helper_enabled", lambda: False)
    monkeypatch.setattr(models.time, "sleep", lambda s: None)       # 502 takes the busy retry first
    refused = _resp(502, "connection")
    refused.headers["x-mind-connection"] = "groq"
    monkeypatch.setattr("httpx.post", lambda *a, **k: refused)
    out = models._try_mind_broker([{"role": "user", "content": "x"}], None, None, 100, None)
    assert out is None and models._last_mind_failure == "mind key refused:Groq"
    assert not models._is_provider_in_cooldown("windy-mind")


# ── N2 Eternitas ─────────────────────────────────────────────────────────────────────────


# ── N3 teams ─────────────────────────────────────────────────────────────────────────────

def test_n3_the_sibling_list_goes_stale_after_five_minutes(monkeypatch):
    teams._reset_for_tests()
    now = [1_000_000.0]
    monkeypatch.setattr(teams.time, "time", lambda: now[0])
    teams._remember([{"matrix_id": "@agent_b:chat"}, {"matrix_id": "@agent_me:chat", "self": True}])
    assert teams.sibling_ids() == frozenset({"@agent_b:chat"})
    now[0] += 299
    assert teams.siblings_stale() is False
    now[0] += 2
    assert teams.siblings_stale() is True
    teams._reset_for_tests()


def test_n3_a_failed_sibling_refresh_keeps_the_last_good_list(monkeypatch):
    teams._reset_for_tests()
    teams._remember([{"matrix_id": "@agent_b:chat"}])

    def down(*a, **k):
        raise RuntimeError("I couldn't reach Windy Chat just now.")

    monkeypatch.setattr(teams, "_call", down)
    teams.refresh_siblings()
    assert teams.sibling_ids() == frozenset({"@agent_b:chat"})
    teams._reset_for_tests()
