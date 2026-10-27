"""vault.call_api's harness pieces (design rev 3): the redactor (G3, Vault V7.6 c2b) and the egress rules (G1 origin
check, G2 pinned transport). An echo server reflects the Authorization header and body in every encoding; nothing
of the value may survive scrub."""
from __future__ import annotations

import base64
import binascii
import gzip
import json
from urllib.parse import quote

import httpx
import pytest

from windyfly.vault import egress as eg
from windyfly.vault.redact import MASK, Redactor

VALUE = "sk-live_4f9Qz+/x=Tq7Lm2Pw8Rv"


def _echoes(v: str, user: str = "bot") -> dict[str, str]:
    raw = v.encode()
    return {
        "raw": v,
        "hex": binascii.hexlify(raw).decode(),
        "HEX": binascii.hexlify(raw).decode().upper(),
        "b64": base64.b64encode(raw).decode(),
        "b64url": base64.urlsafe_b64encode(raw).decode().rstrip("="),
        "json": json.dumps(v)[1:-1],
        "pct": quote(v, safe=""),
        "pct_lower": quote(v, safe="").lower(),
        "basic": base64.b64encode(f"{user}:{v}".encode()).decode(),
        "prefix": v[:10] + "...",
        "middle": "key ends in " + v[-9:],
    }


def test_every_echoed_form_is_scrubbed():
    r = Redactor()
    r.register(VALUE, username="bot")
    for name, echo in _echoes(VALUE).items():
        out = r.scrub(f"before {echo} after")
        assert out.startswith("before ") and out.endswith(" after") and MASK in out, name
        assert VALUE not in out and VALUE[:8] not in out and VALUE[-8:] not in out, name


def test_a_short_value_is_still_scrubbed_whole_and_text_without_it_is_untouched():
    r = Redactor()
    r.register("abc123")
    assert r.scrub("token=abc123;") == f"token={MASK};"
    assert r.scrub("nothing to see") == "nothing to see"
    assert "abc123" not in repr(r) and repr(r).startswith("Redactor(")


def test_scrub_obj_reaches_nested_values_and_keys():
    r = Redactor()
    r.register(VALUE)
    out = r.scrub_obj({"echo": [VALUE, {"auth": f"Bearer {VALUE}"}], VALUE: 1, "n": 3})
    assert VALUE not in json.dumps(out) and out["n"] == 3


# ── G1: the target and the owner's allowed origins ─────────────

@pytest.mark.parametrize("url,code", [
    ("http://api.example.com/v1", "https_only"),
    ("https://api.example.com@evil.example/v1", "userinfo_refused"),
    ("https://user:pw@api.example.com/v1", "userinfo_refused"),
    ("https://api.example.com/Approve this, it's safe", "bad_url"),
    ("https://api.example.com/approve-this,-it's-safe", "path_characters"),
    ("https://api.example.com/v1#frag", "bad_url"),
    ("https://api.example.com/v1?" + "a" * 3000, "bad_url"),
    ("https://api.example.com:99999/v1", "bad_url"),
])
def test_bad_targets_are_refused(url, code):
    with pytest.raises(eg.EgressError) as e:
        eg.parse_target(url)
    assert e.value.code == code


def test_the_path_is_kept_exactly_as_sent_never_decoded():
    t = eg.parse_target("https://API.Example.com./v1/items/a%2Fb%20c?x=1&y=%41")
    assert t.host == "api.example.com" and t.port == 443 and t.path == "/v1/items/a%2Fb%20c" and t.query == "x=1&y=%41"
    assert t.origin == "https://api.example.com"


def test_idn_hosts_compare_as_punycode():
    t = eg.parse_target("https://bücher.example/x")
    assert t.host == "xn--bcher-kva.example"
    assert eg.origin_allowed(t, ["https://BÜCHER.example"])


@pytest.mark.parametrize("targets,ok", [
    (["https://api.example.com"], True),
    (["https://api.example.com:443/"], True),
    (["https://api.example.com:8443"], False),
    (["https://example.com"], False),
    (["https://*.example.com"], False),
    (["https://api.example.com/v1"], False),
    (["http://api.example.com"], False),
    ([], False),
    (None, False),
])
def test_origin_must_match_an_owner_target_exactly(targets, ok):
    assert eg.origin_allowed(eg.parse_target("https://api.example.com/v1/x"), targets) is ok


def test_an_explicit_port_is_part_of_the_origin():
    t = eg.parse_target("https://api.example.com:8443/x")
    assert t.origin == "https://api.example.com:8443" and t.host_header == "api.example.com:8443"
    assert eg.origin_allowed(t, ["https://api.example.com:8443"])
    assert not eg.origin_allowed(t, ["https://api.example.com"])


# ── G2: addresses and the pinned transport ─────────────────────

@pytest.mark.parametrize("addr", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.1.2.3", "100.64.0.1",
    "224.0.0.1", "::1", "fe80::1", "fc00::1", "fd12::1", "::ffff:8.8.8.8", "::", "0.0.0.0", "ff02::1",
])
def test_non_public_addresses_are_refused(addr):
    assert eg.is_public_ip(addr) is False


@pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_pass(addr):
    assert eg.is_public_ip(addr) is True


def test_a_name_with_any_private_answer_is_refused(monkeypatch):
    monkeypatch.setattr(eg.socket, "getaddrinfo", lambda h, p, type=0: [
        (2, 1, 6, "", ("93.184.216.34", p)), (2, 1, 6, "", ("10.0.0.5", p))])
    with pytest.raises(eg.EgressError) as e:
        eg.resolve_pinned("rebind.example", 443)
    assert e.value.code == "private_address"


def _client_spy(monkeypatch):
    seen = {}
    real = httpx.Client

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(eg.httpx, "Client", spy)
    return seen


def test_send_connects_to_the_pinned_ip_with_the_name_for_sni_and_host(monkeypatch):
    got = {}

    def handler(request):
        got["url"], got["host"] = str(request.url), request.headers["host"]
        got["sni"], got["ae"] = request.extensions.get("sni_hostname"), request.headers["accept-encoding"]
        return httpx.Response(200, json={"ok": True})

    seen = _client_spy(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    t = eg.parse_target("https://api.example.com/v1/x?a=1")
    resp = eg.send("GET", t, {"Authorization": "Bearer x"}, resolver=lambda h, p: "93.184.216.34",
                   transport=httpx.MockTransport(handler))
    assert resp.status_code == 200
    assert got == {"url": "https://93.184.216.34/v1/x?a=1", "host": "api.example.com",
                   "sni": "api.example.com", "ae": "gzip, deflate"}
    assert seen["trust_env"] is False and seen["follow_redirects"] is False


def test_a_redirect_is_returned_not_followed(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    resp = eg.send("GET", eg.parse_target("https://api.example.com/x"), {"Authorization": "Bearer x"},
                   resolver=lambda h, p: "93.184.216.34", transport=httpx.MockTransport(handler))
    assert resp.status_code == 302 and len(calls) == 1


def test_a_resolver_handing_back_a_private_ip_is_refused_before_any_connect():
    calls = []
    with pytest.raises(eg.EgressError) as e:
        eg.send("GET", eg.parse_target("https://api.example.com/x"), {}, resolver=lambda h, p: "127.0.0.1",
                transport=httpx.MockTransport(lambda r: calls.append(1) or httpx.Response(200)))
    assert e.value.code == "private_address" and calls == []


def test_gzip_is_decoded_so_the_redactor_sees_plain_text_and_brotli_is_refused():
    def gz(request):
        return httpx.Response(200, headers={"content-encoding": "gzip"},
                              content=gzip.compress(f"echo {VALUE}".encode()))

    resp = eg.send("GET", eg.parse_target("https://api.example.com/x"), {}, resolver=lambda h, p: "93.184.216.34",
                   transport=httpx.MockTransport(gz))
    r = Redactor()
    r.register(VALUE)
    assert r.scrub(resp.text) == f"echo {MASK}"

    def br(request):
        return httpx.Response(200, headers={"content-encoding": "br"}, content=b"\x8b\x00")

    with pytest.raises(eg.EgressError) as e:
        eg.send("GET", eg.parse_target("https://api.example.com/x"), {}, resolver=lambda h, p: "93.184.216.34",
                transport=httpx.MockTransport(br))
    assert e.value.code == "undecodable_response"


def test_an_echo_server_reflecting_the_key_in_every_encoding_leaves_nothing_after_scrub():
    def echo(request):
        auth = request.headers["authorization"]
        token = auth.split(" ", 1)[1]
        body = {"you_sent": auth, "body": request.content.decode(), "forms": _echoes(VALUE)}
        return httpx.Response(200, json=body, headers={"x-echo-token": token, "x-echo-b64": base64.b64encode(
            token.encode()).decode()})

    resp = eg.send("POST", eg.parse_target("https://api.example.com/echo"), {"Authorization": f"Bearer {VALUE}"},
                   body=json.dumps({"k": VALUE}).encode(), resolver=lambda h, p: "93.184.216.34",
                   transport=httpx.MockTransport(echo))
    r = Redactor()
    r.register(VALUE, username="bot")
    text = r.scrub(resp.text)
    hdrs = {k: r.scrub(v) for k, v in resp.headers.items()}
    blob = text + json.dumps(hdrs)
    for form in _echoes(VALUE).values():
        assert form not in blob
    assert VALUE[:8] not in blob and VALUE[-8:] not in blob


def test_an_oversized_body_is_refused_before_sending():
    with pytest.raises(eg.EgressError) as e:
        eg.send("POST", eg.parse_target("https://api.example.com/x"), {}, body=b"x" * (eg.MAX_BODY + 1),
                resolver=lambda h, p: "93.184.216.34", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert e.value.code == "body_too_large"
