"""Contract tests for the SSRF-safe fetcher (P0-S4 fix).

Proves:
  - http(s) only; file://, gopher://, ftp://, data://, etc. refused.
  - Loopback (127/8, ::1), RFC1918, CGNAT, link-local (including
    EC2 IMDS 169.254.169.254), IPv6 ULA, multicast — all refused.
  - Bare IPs are validated as strictly as hostnames.
  - DNS rebinding: if any A/AAAA is on the blocklist, refuse.
  - Redirects are NOT auto-followed; opting in re-validates the
    Location before the second fetch.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import httpx
import pytest
import respx

from windyfly.safe_fetch import SSRFBlocked, _is_blocked_ip, _validate_url, safe_fetch


def _stub_dns(ip: str):
    """Make getaddrinfo resolve to exactly `ip`."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return patch(
        "windyfly.safe_fetch.socket.getaddrinfo",
        return_value=[(family, socket.SOCK_STREAM, 0, "", (ip, 0))],
    )


class TestScheme:
    def test_http_and_https_allowed(self):
        with _stub_dns("93.184.216.34"):
            _validate_url("http://example.com/")
            _validate_url("https://example.com/")

    @pytest.mark.parametrize("scheme", [
        "file:///etc/passwd",
        "ftp://ftp.example.com/",
        "gopher://example.com/",
        "data:text/plain,hello",
        "javascript:alert(1)",
    ])
    def test_other_schemes_refused(self, scheme):
        with pytest.raises(SSRFBlocked) as exc:
            _validate_url(scheme)
        assert "scheme" in str(exc.value)


class TestForbiddenRanges:
    @pytest.mark.parametrize("ip", [
        "127.0.0.1",
        "127.42.0.1",
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.0.1",
        "192.168.1.1",
        "169.254.169.254",   # the EC2 IMDS, the whole reason for this PR
        "100.64.0.1",         # CGNAT
        "0.0.0.0",
        "255.255.255.255",
        "224.0.0.1",          # multicast
    ])
    def test_blocked_v4(self, ip):
        assert _is_blocked_ip(ip), f"{ip} should be blocked"

    @pytest.mark.parametrize("ip", [
        "::1",                          # loopback
        "fc00::1",                       # ULA
        "fe80::1",                       # link-local
        "ff02::1",                       # multicast
        "::ffff:127.0.0.1",             # IPv4-mapped loopback
    ])
    def test_blocked_v6(self, ip):
        assert _is_blocked_ip(ip), f"{ip} should be blocked"

    @pytest.mark.parametrize("ip", [
        "8.8.8.8",
        "93.184.216.34",
        "1.1.1.1",
        "2606:4700:4700::1111",
    ])
    def test_public_ips_allowed(self, ip):
        assert not _is_blocked_ip(ip)

    def test_hostname_resolving_to_blocked_ip_refused(self):
        with _stub_dns("169.254.169.254"):
            with pytest.raises(SSRFBlocked) as exc:
                _validate_url("http://metadata.example/latest/meta-data/")
            assert "169.254.169.254" in str(exc.value)

    def test_bare_loopback_literal_refused(self):
        # No DNS stub — host is the IP, getaddrinfo returns it as-is.
        with pytest.raises(SSRFBlocked):
            _validate_url("http://127.0.0.1:8500/api/v1/trust/ET-X")

    def test_dns_rebinding_refused_when_any_record_blocked(self):
        # Two A records; one public, one loopback. Must refuse.
        with patch(
            "windyfly.safe_fetch.socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
            ],
        ):
            with pytest.raises(SSRFBlocked):
                _validate_url("http://multi.example/")


class TestFetchBehaviour:
    @respx.mock
    def test_fetches_public_url(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="hello public")
        )
        with _stub_dns("93.184.216.34"):
            r = safe_fetch("https://example.com/")
        assert r.status_code == 200
        assert r.text == "hello public"

    @respx.mock
    def test_does_not_auto_follow_redirect(self):
        # Server tries to redirect to an internal URL; without
        # allow_one_redirect we must NOT chase it.
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                302, headers={"Location": "http://127.0.0.1:8500/secret"}
            )
        )
        with _stub_dns("93.184.216.34"):
            r = safe_fetch("https://example.com/")
        # Stops at the 302 — no fetch of the internal target.
        assert r.status_code == 302

    @respx.mock
    def test_allowed_redirect_revalidates_target(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
            )
        )
        # First call: public. Second call: would be blocked, must raise.
        with patch(
            "windyfly.safe_fetch.socket.getaddrinfo",
            side_effect=[
                [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))],
                [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))],
            ],
        ):
            with pytest.raises(SSRFBlocked):
                safe_fetch("https://example.com/", allow_one_redirect=True)

    @respx.mock
    def test_allowed_redirect_to_another_public_url(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                301, headers={"Location": "https://final.example/"}
            )
        )
        respx.get("https://final.example/").mock(
            return_value=httpx.Response(200, text="after redirect")
        )
        with patch(
            "windyfly.safe_fetch.socket.getaddrinfo",
            side_effect=[
                [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))],
                [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.2.3.4", 0))],
            ],
        ):
            r = safe_fetch("https://example.com/", allow_one_redirect=True)
        assert r.status_code == 200
        assert r.text == "after redirect"

    def test_timeout_bounds_enforced(self):
        with pytest.raises(ValueError):
            safe_fetch("http://example.com/", timeout=0)
        with pytest.raises(ValueError):
            safe_fetch("http://example.com/", timeout=120)

    def test_dns_failure_refused(self):
        with patch(
            "windyfly.safe_fetch.socket.getaddrinfo",
            side_effect=socket.gaierror("nodename nor servname provided"),
        ):
            with pytest.raises(SSRFBlocked) as exc:
                safe_fetch("http://this-does-not-resolve.invalid/")
            assert "DNS" in str(exc.value)
