"""Wave 10: tests for `windy selftest --full` ecosystem health checks."""

from __future__ import annotations

import pytest

from windyfly import cli_selftest
from windyfly.cli_selftest import (
    EcosystemCheck,
    _build_ecosystem_checks,
    _dispatch_checks,
    run_ecosystem_health,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in (
        "ETERNITAS_API_URL", "ETERNITAS_URL", "ETERNITAS_PASSPORT",
        "WINDY_PRO_URL", "WINDY_API_URL",
        "MATRIX_HOMESERVER", "WINDYMAIL_API_URL", "WINDY_CLOUD_URL",
    ):
        monkeypatch.delenv(key, raising=False)


# ─── _build_ecosystem_checks ─────────────────────────────────────────


def test_no_env_no_checks() -> None:
    assert _build_ecosystem_checks() == []


def test_eternitas_without_passport_hits_health(monkeypatch) -> None:
    monkeypatch.setenv("ETERNITAS_API_URL", "https://eternitas.ai")
    checks = _build_ecosystem_checks()
    assert len(checks) == 1
    assert checks[0].name == "Eternitas"
    assert checks[0].url == "https://eternitas.ai/health"
    assert checks[0].critical is True


def test_eternitas_with_passport_hits_registry_verify(monkeypatch) -> None:
    monkeypatch.setenv("ETERNITAS_API_URL", "https://eternitas.ai")
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-ABC-DEF")
    (c,) = _build_ecosystem_checks()
    assert c.url == "https://eternitas.ai/api/v1/registry/verify/ET26-ABC-DEF"
    assert c.critical is True


def test_pro_and_matrix_and_mail_and_cloud(monkeypatch) -> None:
    monkeypatch.setenv("WINDY_PRO_URL", "https://windypro.com")
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.windyword.ai")
    monkeypatch.setenv("WINDYMAIL_API_URL", "https://mail.windymail.ai")
    monkeypatch.setenv("WINDY_CLOUD_URL", "https://cloud.windyword.ai")

    by_name = {c.name: c for c in _build_ecosystem_checks()}

    assert by_name["Windy Pro"].url == "https://windypro.com/healthz"
    assert by_name["Windy Pro"].critical is True
    assert by_name["Windy Chat"].url == (
        "https://chat.windyword.ai/_matrix/client/versions"
    )
    assert by_name["Windy Chat"].critical is False  # optional channel
    assert by_name["Windy Mail"].url == "https://mail.windymail.ai/healthz"
    assert by_name["Windy Mail"].critical is False
    assert by_name["Windy Cloud"].url == "https://cloud.windyword.ai/healthz"
    assert by_name["Windy Cloud"].critical is False


def test_trailing_slashes_are_stripped(monkeypatch) -> None:
    monkeypatch.setenv("WINDY_PRO_URL", "https://windypro.com/////")
    (c,) = _build_ecosystem_checks()
    assert c.url == "https://windypro.com/healthz"


# ─── _dispatch_checks ───────────────────────────────────────────────


class _FakeResp:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeHttpx:
    """Stand-in for the `httpx` module in cli_selftest."""

    def __init__(self, status_by_url: dict[str, int]):
        self.status_by_url = status_by_url
        self.called: list[str] = []

    def get(self, url: str, timeout: float):
        self.called.append(url)
        status = self.status_by_url.get(url)
        if status is None:
            raise RuntimeError(f"unexpected URL: {url}")
        return _FakeResp(status)


def test_dispatch_populates_ok_and_latency(monkeypatch) -> None:
    fake = _FakeHttpx({"https://eternitas.ai/health": 200})
    monkeypatch.setitem(cli_selftest.__dict__, "httpx", fake)
    # We also need the import inside _dispatch_checks to resolve `fake`.
    # It imports httpx locally, so intercept via sys.modules.
    import sys
    monkeypatch.setitem(sys.modules, "httpx", fake)

    checks = [EcosystemCheck(
        name="Eternitas", url="https://eternitas.ai/health", critical=True,
    )]
    _dispatch_checks(checks, timeout=1.0)
    assert checks[0].ok is True
    assert checks[0].detail == "HTTP 200"
    assert checks[0].latency_ms >= 0


def test_dispatch_treats_under_500_as_ok(monkeypatch) -> None:
    """Eternitas /registry/verify/<unknown> returns 404 — still reachable,
    still a proof-of-life. ≥500 is the only hard failure."""
    import sys
    fake = _FakeHttpx({"https://eternitas.ai/api/v1/registry/verify/NONE": 404})
    monkeypatch.setitem(sys.modules, "httpx", fake)

    checks = [EcosystemCheck(
        name="Eternitas",
        url="https://eternitas.ai/api/v1/registry/verify/NONE",
        critical=True,
    )]
    _dispatch_checks(checks, timeout=1.0)
    assert checks[0].ok is True

    # And now a 500 must flip it red.
    fake_500 = _FakeHttpx({"https://x/healthz": 503})
    monkeypatch.setitem(sys.modules, "httpx", fake_500)
    checks2 = [EcosystemCheck(name="X", url="https://x/healthz", critical=True)]
    _dispatch_checks(checks2, timeout=1.0)
    assert checks2[0].ok is False


def test_dispatch_network_error_is_handled(monkeypatch) -> None:
    import sys

    class _BoomHttpx:
        def get(self, url: str, timeout: float):
            raise TimeoutError("network went away")

    monkeypatch.setitem(sys.modules, "httpx", _BoomHttpx())

    checks = [EcosystemCheck(name="X", url="https://x/health", critical=True)]
    _dispatch_checks(checks, timeout=1.0)
    assert checks[0].ok is False
    assert "TimeoutError" in checks[0].detail


# ─── run_ecosystem_health ───────────────────────────────────────────


def test_run_health_returns_true_when_all_critical_ok(monkeypatch) -> None:
    import sys
    monkeypatch.setenv("WINDY_PRO_URL", "https://windypro.com")
    fake = _FakeHttpx({"https://windypro.com/healthz": 200})
    monkeypatch.setitem(sys.modules, "httpx", fake)

    assert run_ecosystem_health(timeout=1.0) is True


def test_run_health_returns_false_when_critical_red(monkeypatch) -> None:
    import sys
    monkeypatch.setenv("WINDY_PRO_URL", "https://windypro.com")
    fake = _FakeHttpx({"https://windypro.com/healthz": 503})
    monkeypatch.setitem(sys.modules, "httpx", fake)

    assert run_ecosystem_health(timeout=1.0) is False


def test_run_health_ignores_warning_on_optional_service(monkeypatch) -> None:
    """Matrix is optional — a red Matrix alone must not flip the overall
    return value to False."""
    import sys
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.windyword.ai")
    fake = _FakeHttpx({"https://chat.windyword.ai/_matrix/client/versions": 503})
    monkeypatch.setitem(sys.modules, "httpx", fake)

    assert run_ecosystem_health(timeout=1.0) is True
