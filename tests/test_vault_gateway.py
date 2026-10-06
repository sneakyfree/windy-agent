"""Strand D3.1: the egress gateway FRAME (dark; fake leases and a mock transport, no network)."""

from __future__ import annotations

import socket

import httpx
import pytest

from windyfly.vault import egress_guard as eg
from windyfly.vault import gateway as gw
from windyfly.vault import redactor
from windyfly.vault.limiter import Limited, ProviderLimiter

TOKEN = "ghs_leased_installation_token_0123456789"
GITHUB = gw.ProviderEntry(id="github", hosts=("api.github.com",), auth_style="bearer", enabled=True)


class FakeLeases:
    def __init__(self, value=TOKEN, host="api.github.com"):
        self.value, self.host, self.calls = value, host, []

    def get(self, connection_id, *, force=False):
        self.calls.append((connection_id, force))
        return gw.Lease(value=self.value if not force else self.value + "R", host=self.host)


def _resolver(*ips):
    return lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]


def _gateway(handler, *, entries=None, leases=None, resolver=None):
    return gw.Gateway(entries=entries or {"github": GITHUB}, leases=leases or FakeLeases(),
                      resolver=resolver or _resolver("140.82.112.3"), transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv(gw.ENV_FLAG, "1")
    redactor.clear()
    yield
    redactor.clear()


def _ok(request):
    return httpx.Response(200, json={"login": "octo"})


def test_off_by_default(monkeypatch):
    monkeypatch.delenv(gw.ENV_FLAG)
    with pytest.raises(gw.GatewayDenied) as e:
        _gateway(_ok).call("c1", "github", "GET", "/user")
    assert e.value.code == "egress_off"


def test_injects_the_lease_pins_the_ip_and_keeps_the_real_host():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), host=request.headers["host"], auth=request.headers["authorization"],
                    sni=request.extensions.get("sni_hostname"))
        return httpx.Response(200, json={"login": "octo"})

    out = _gateway(handler).call("c1", "github", "GET", "/user", headers={"X-Evil": "1", "Accept": "application/json"})
    assert out["ok"] and out["status"] == 200 and '"octo"' in out["body"]
    assert seen["url"].startswith("https://140.82.112.3/user")        # pinned address (default port elided)
    assert seen["host"] == "api.github.com" and seen["sni"] == "api.github.com"
    assert seen["auth"] == f"Bearer {TOKEN}"


def test_caller_headers_are_an_allow_list():
    got = {}

    def handler(request):
        got.update(request.headers)
        return httpx.Response(200, text="{}")

    _gateway(handler).call("c1", "github", "GET", "/user",
                           headers={"Authorization": "Bearer attacker", "X-Evil": "1", "Accept": "x/y"})
    assert got["authorization"] == f"Bearer {TOKEN}" and "x-evil" not in got and got["accept"] == "x/y"


@pytest.mark.parametrize("path", ["user", "/a/../b", "//evil.com/x", "/x\n/y", "/x y", "/" + "a" * 3000])
def test_bad_paths_refused(path):
    with pytest.raises(gw.GatewayDenied) as e:
        _gateway(_ok).call("c1", "github", "GET", path)
    assert e.value.code == "bad_path"


def test_only_catalog_hosts_and_enabled_providers():
    with pytest.raises(eg.EgressDenied):
        _gateway(_ok).call("c1", "github", "GET", "/x", host="evil.com")
    off = {"github": gw.ProviderEntry(id="github", hosts=("api.github.com",), enabled=False)}
    with pytest.raises(gw.GatewayDenied) as e:
        _gateway(_ok, entries=off).call("c1", "github", "GET", "/x")
    assert e.value.code == "provider_unavailable"
    with pytest.raises(gw.GatewayDenied):
        _gateway(_ok).call("c1", "nope", "GET", "/x")


def test_method_allow_list():
    with pytest.raises(gw.GatewayDenied):
        _gateway(_ok).call("c1", "github", "TRACE", "/x")


def test_private_resolution_is_refused():
    with pytest.raises(eg.EgressDenied) as e:
        _gateway(_ok, resolver=_resolver("169.254.169.254")).call("c1", "github", "GET", "/x")
    assert e.value.code == "private_address"


def test_redirects_are_never_followed():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://evil.com/steal"})

    with pytest.raises(eg.EgressDenied) as e:
        _gateway(handler).call("c1", "github", "GET", "/x")
    assert e.value.code == "redirect_refused"


def test_a_lease_for_another_host_is_refused():
    with pytest.raises(gw.GatewayDenied) as e:
        _gateway(_ok, leases=FakeLeases(host="api.cloudflare.com")).call("c1", "github", "GET", "/x")
    assert e.value.code == "lease_host_mismatch"


def test_401_releases_once_and_retries():
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(401 if len(seen) == 1 else 200, text="{}")

    leases = FakeLeases()
    out = _gateway(handler, leases=leases).call("c1", "github", "GET", "/x")
    assert out["status"] == 200 and leases.calls == [("c1", False), ("c1", True)]
    assert seen == [f"Bearer {TOKEN}", f"Bearer {TOKEN}R"]


def test_a_response_carrying_the_lease_or_a_secret_shape_is_blocked():
    with pytest.raises(gw.GatewayDenied) as e:
        _gateway(lambda r: httpx.Response(200, text=f"remote: https://x:{TOKEN}@github.com")).call("c1", "github", "GET", "/x")
    assert e.value.code == "secret_in_response"
    with pytest.raises(gw.GatewayDenied):
        _gateway(lambda r: httpx.Response(200, text="key ghp_" + "a" * 36)).call("c1", "github", "GET", "/x")
    # docs that merely mention auth are fine
    out = _gateway(lambda r: httpx.Response(200, text="Use an Authorization: Bearer header")).call("c1", "github", "GET", "/x")
    assert out["ok"]


def test_oversized_responses_are_refused():
    g = _gateway(lambda r: httpx.Response(200, content=b"x" * 5000))
    g.max_bytes = 1000
    with pytest.raises(gw.GatewayDenied) as e:
        g.call("c1", "github", "GET", "/x")
    assert e.value.code == "response_too_large"


def test_the_lease_is_registered_with_the_redactor():
    _gateway(_ok).call("c1", "github", "GET", "/user")
    assert TOKEN not in redactor.redact(f"echo {TOKEN}")


def test_provider_limiter_applies_and_feeds_back():
    g = _gateway(lambda r: httpx.Response(429, headers={"retry-after": "30"}, text="{}"))
    g.limiters["github:c1"] = ProviderLimiter()
    g.call("c1", "github", "GET", "/x")
    with pytest.raises(Limited):
        g.call("c1", "github", "GET", "/x")


@pytest.mark.parametrize("style,name,check", [
    ("header", "X-Api-Key", lambda r: r.headers["x-api-key"] == TOKEN),
    ("basic", "", lambda r: r.headers["authorization"].startswith("Basic ")),
    ("query", "key", lambda r: f"key={TOKEN}" in str(r.url)),
])
def test_auth_styles(style, name, check):
    seen = []

    def handler(request):
        seen.append(check(request))
        return httpx.Response(200, text="{}")

    e = {"github": gw.ProviderEntry(id="github", hosts=("api.github.com",), auth_style=style, auth_name=name, enabled=True)}
    _gateway(handler, entries=e).call("c1", "github", "GET", "/x", query={"a": "1"})
    assert seen == [True]


def test_unsupported_auth_style_fails_closed():
    e = {"github": gw.ProviderEntry(id="github", hosts=("api.github.com",), auth_style="sigv4", enabled=True)}
    with pytest.raises(gw.GatewayDenied) as ex:
        _gateway(_ok, entries=e).call("c1", "github", "GET", "/x")
    assert ex.value.code == "auth_style_unsupported"
