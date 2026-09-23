"""A clean install hatches against the real Eternitas issuer by default.

Clean-machine journey test (2026-09-23): `windy go` with no ETERNITAS_URL
refused ("No Eternitas issuer is configured") and the user never got a
passport, a birth certificate or the free Mind brain.
"""
from __future__ import annotations

import pytest

from windyfly.eternitas.url import DEFAULT_ETERNITAS_URL, issuer_url, resolve_eternitas_url


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("ETERNITAS_URL", "ETERNITAS_API_URL", "WINDYFLY_ALLOW_FAKE_IDENTITY"):
        monkeypatch.delenv(k, raising=False)


def test_default_is_the_production_issuer():
    assert DEFAULT_ETERNITAS_URL == "https://api.eternitas.ai"
    assert issuer_url() == "https://api.eternitas.ai"


def test_explicit_env_wins(monkeypatch):
    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.example/")
    assert issuer_url() == "https://eternitas.example"


def test_config_wins_over_env(monkeypatch):
    monkeypatch.setenv("ETERNITAS_URL", "https://env.example")
    assert issuer_url({"ecosystem": {"eternitas_url": "https://cfg.example"}}) == "https://cfg.example"


@pytest.mark.parametrize("off", ["off", "OFF", "none", "disabled", "0"])
def test_switched_off(monkeypatch, off):
    monkeypatch.setenv("ETERNITAS_URL", off)
    assert issuer_url() == ""
    assert resolve_eternitas_url("https://x") == ""  # off beats any default too


def test_mock_opt_in_keeps_the_local_lane(monkeypatch):
    # Tests and offline developers opt into the mock explicitly; a production
    # default must not override that.
    monkeypatch.setenv("WINDYFLY_ALLOW_FAKE_IDENTITY", "1")
    assert issuer_url() == ""
    monkeypatch.setenv("ETERNITAS_URL", "https://real.example")
    assert issuer_url() == "https://real.example"  # explicit still wins
