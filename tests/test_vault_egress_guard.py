"""Strand D3.2: where the gateway may connect."""

from __future__ import annotations

import socket

import pytest

from windyfly.vault import egress_guard as eg


def _resolver(*ips):
    return lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]


@pytest.mark.parametrize("host", ["api.github.com", "a-b.example.co.uk"])
def test_valid_exact_hostnames(host):
    assert eg.valid_hostname(host)


@pytest.mark.parametrize("host", ["*.github.com", "github.com.", "127.0.0.1", "[::1]", "localhost", "a b.com", "", "git hub.com"])
def test_wildcards_ip_literals_and_junk_are_refused(host):
    assert not eg.valid_hostname(host)


@pytest.mark.parametrize("ip", ["10.0.0.5", "192.168.1.1", "127.0.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
                                "::1", "fd00::1", "fe80::1", "::ffff:127.0.0.1", "224.0.0.1"])
def test_non_public_addresses(ip):
    assert not eg.is_public_ip(ip)


def test_public_addresses():
    assert eg.is_public_ip("140.82.112.3") and eg.is_public_ip("2606:4700:4700::1111")


def test_check_url_rules():
    ok = ("api.github.com",)
    assert eg.check_url("https://api.github.com/user", ok).hostname == "api.github.com"
    for bad in ("http://api.github.com/x", "https://user:pw@api.github.com/x", "https://evil.com/x",
                "https://api.github.com:8443/x", "https://api.github.com.evil.com/x"):
        with pytest.raises(eg.EgressDenied):
            eg.check_url(bad, ok)


def test_resolution_must_be_all_public_and_is_pinned():
    t = eg.target_for("https://api.github.com/user?x=1", ("api.github.com",), resolver=_resolver("140.82.112.3"))
    assert t.ip == "140.82.112.3" and t.path_query == "/user?x=1"
    with pytest.raises(eg.EgressDenied) as e:
        eg.target_for("https://api.github.com/", ("api.github.com",), resolver=_resolver("140.82.112.3", "169.254.169.254"))
    assert e.value.code == "private_address"      # one bad answer is enough (rebinding)


def test_dns_failure_is_plain():
    def boom(*a, **k):
        raise OSError("nx")

    with pytest.raises(eg.EgressDenied) as e:
        eg.resolve_public("api.github.com", 443, boom)
    assert e.value.code == "dns_failed"


def test_redirects_stay_on_the_same_host_over_https():
    eg.check_redirect("https://api.github.com/a", "https://api.github.com/b")
    for to in ("https://evil.com/b", "http://api.github.com/b", "https://api.github.com:444/b"):
        with pytest.raises(eg.EgressDenied):
            eg.check_redirect("https://api.github.com/a", to)
