"""vault.call_api's harness pieces (design rev 3): the redactor (G3, Vault V7.6 c2b) and the egress rules (G1 origin
check, G2 pinned transport). An echo server reflects the Authorization header and body in every encoding; nothing
of the value may survive scrub."""
from __future__ import annotations

import base64
import binascii
import gzip
import json
import re
from urllib.parse import quote

import httpx
import pytest

from windyfly.vault import egress as eg
from windyfly.vault.redact import MASK, MIN_SECRET, Redactor, SecretTooShort

VALUE = "sk-live_4f9Qz+/x=Tq7Lm2Pw8Rv"


def _echoes(v: str, user: str = "bot") -> dict[str, str]:
    raw = v.encode()
    pct = quote(v, safe="")
    return {
        "raw": v,
        "hex": binascii.hexlify(raw).decode(),
        "HEX": binascii.hexlify(raw).decode().upper(),
        "b64": base64.b64encode(raw).decode(),
        "b64_nopad": base64.b64encode(raw).decode().rstrip("="),
        "b64url": base64.urlsafe_b64encode(raw).decode().rstrip("="),
        "json": json.dumps(v)[1:-1],
        "json_unicode": json.dumps(v, ensure_ascii=False)[1:-1],  # JSON.stringify leaves non-ASCII as-is
        "json_upper_u": re.sub(r"\\u[0-9a-f]{4}", lambda m: "\\u" + m.group(0)[2:].upper(), json.dumps(v)[1:-1]),
        "pct": pct,
        "pct_lower": re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), pct),
        "basic": base64.b64encode(f"{user}:{v}".encode()).decode(),
    }


def _survivors(out: str, v: str = VALUE) -> list[str]:
    """Any echoed form, or the value's base64 at any byte offset, still readable in ``out``."""
    left = [name for name, form in _echoes(v).items() if form in out]
    for k in (0, 1, 2):
        for enc in (base64.b64encode, base64.urlsafe_b64encode):
            text = enc(b"\0" * k + v.encode()).decode().rstrip("=")
            stable = text[-(-4 * k // 3):((k + len(v)) * 8) // 6]
            if stable in out:
                left.append(f"{enc.__name__}@{k}")
    return left


# A second value whose JSON, percent and hex forms all differ from the raw one (quote, backslash, non-ASCII).
VALUE2 = 'pw"q\\back/sl\u00e9-0123456789abc'


@pytest.mark.parametrize("value", [VALUE, VALUE2])
@pytest.mark.parametrize("name", list(_echoes(VALUE)))
@pytest.mark.parametrize("where", ["{}", "a{}", "start {} end", "{}{}", "x=\"{}\";y"])
def test_no_echoed_form_survives_in_any_position(value, name, where):
    r = Redactor()
    r.register(value, username="bot")
    out = r.scrub(where.format(*[_echoes(value)[name]] * where.count("{}")))
    assert _survivors(out, value) == [] and MASK in out


def test_the_second_value_really_has_distinct_forms():
    forms = _echoes(VALUE2)
    assert len({VALUE2, forms["json"], forms["json_unicode"], forms["json_upper_u"]}) == 4
    assert forms["json"] != VALUE2 and forms["pct"] != forms["pct_lower"] and forms["hex"] != forms["HEX"]


@pytest.mark.parametrize("k", [0, 1, 2, 3, 4, 5])
@pytest.mark.parametrize("enc", [base64.b64encode, base64.urlsafe_b64encode])
def test_the_value_inside_a_larger_base64_blob_is_masked_at_every_byte_offset(k, enc):
    r = Redactor()
    r.register(VALUE)
    blob = enc(b"Q" * k + b'{"token":"' + VALUE.encode() + b'","n":1}').decode()
    out = r.scrub(blob)
    assert MASK in out and _survivors(out) == []


def test_only_whole_forms_are_masked_never_a_fragment():
    """No oracle (Hub on #655): a guess that is a piece of the value comes back unchanged, so masking reveals nothing."""
    r = Redactor()
    r.register(VALUE)
    for guess in (VALUE[:8], VALUE[:-1], VALUE[3:20], "sk-live_", VALUE[-4:]):
        assert r.scrub(f"q={guess}") == f"q={guess}"


def test_a_short_secret_is_refused_at_registration():
    r = Redactor()
    with pytest.raises(SecretTooShort):
        r.register("abc123")
    with pytest.raises(SecretTooShort):
        r.register("x" * (MIN_SECRET - 1))
    r.register("x" * MIN_SECRET)
    assert "x" * MIN_SECRET not in repr(r) and repr(r).startswith("Redactor(")


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
    ("https://api.example.com/a/../admin", "dot_segments"),
    ("https://api.example.com/a/./b", "dot_segments"),
    ("https://api.example.com/a/%2e%2E/admin", "dot_segments"),
    ("https://api.example.com/x?note=Approve(this)", "path_characters"),
    ("https://api.example.com/x?a=1;b=2", "path_characters"),
    ("https://api.example.com/x?msg=it's-safe", "path_characters"),
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
    resp, _ = eg.send("GET", t, {"Authorization": "Bearer x"}, resolver=lambda h, p: "93.184.216.34",
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

    resp, _ = eg.send("GET", eg.parse_target("https://api.example.com/x"), {"Authorization": "Bearer x"},
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

    _, body = eg.send("GET", eg.parse_target("https://api.example.com/x"), {},
                      resolver=lambda h, p: "93.184.216.34", transport=httpx.MockTransport(gz))
    r = Redactor()
    r.register(VALUE)
    assert r.scrub(body.decode()) == f"echo {MASK}"

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

    resp, raw = eg.send("POST", eg.parse_target("https://api.example.com/echo"), {"Authorization": f"Bearer {VALUE}"},
                   body=json.dumps({"k": VALUE}).encode(), resolver=lambda h, p: "93.184.216.34",
                   transport=httpx.MockTransport(echo))
    r = Redactor()
    r.register(VALUE, username="bot")
    text = r.scrub(raw.decode())
    hdrs = {k: r.scrub(v) for k, v in resp.headers.items()}
    blob = text + json.dumps(hdrs)
    assert _survivors(blob) == [] and MASK in blob


def test_an_oversized_body_is_refused_before_sending():
    with pytest.raises(eg.EgressError) as e:
        eg.send("POST", eg.parse_target("https://api.example.com/x"), {}, body=b"x" * (eg.MAX_BODY + 1),
                resolver=lambda h, p: "93.184.216.34", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert e.value.code == "body_too_large"


def test_the_query_allowlist_keeps_ordinary_queries():
    t = eg.parse_target("https://api.example.com/v1/x?page=2&per_page=50&sort=-created,name&q=a+b%20c&ts=1.5~x")
    assert t.query == "page=2&per_page=50&sort=-created,name&q=a+b%20c&ts=1.5~x"


def test_ipv6_dns_answers_are_checked_like_ipv4(monkeypatch):
    monkeypatch.setattr(eg.socket, "getaddrinfo", lambda h, p, type=0: [
        (10, 1, 6, "", ("2606:4700:4700::1111", p, 0, 0)), (10, 1, 6, "", ("fd00::5", p, 0, 0))])
    with pytest.raises(eg.EgressError) as e:
        eg.resolve_pinned("v6.example", 443)
    assert e.value.code == "private_address"
    monkeypatch.setattr(eg.socket, "getaddrinfo", lambda h, p, type=0: [
        (10, 1, 6, "", ("2606:4700:4700::1111%eth0", p, 0, 0))])
    assert eg.resolve_pinned("v6.example", 443) == "2606:4700:4700::1111"


def test_an_ipv6_pin_is_bracketed_in_the_url():
    got = {}

    def handler(request):
        got["url"] = str(request.url)
        return httpx.Response(200)

    eg.send("GET", eg.parse_target("https://api.example.com/x"), {}, resolver=lambda h, p: "2606:4700:4700::1111",
            transport=httpx.MockTransport(handler))
    assert got["url"] == "https://[2606:4700:4700::1111]/x"


def test_a_gzip_bomb_stops_at_the_decoded_cap():
    bomb = gzip.compress(b"A" * (eg.MAX_RESPONSE + 4096))
    assert len(bomb) < 20000

    def handler(request):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=bomb)

    with pytest.raises(eg.EgressError) as e:
        eg.send("GET", eg.parse_target("https://api.example.com/x"), {}, resolver=lambda h, p: "93.184.216.34",
                transport=httpx.MockTransport(handler))
    assert e.value.code == "response_too_large"


def test_certificate_checks_cannot_be_switched_off(monkeypatch):
    with pytest.raises(ValueError):
        eg.send("GET", eg.parse_target("https://api.example.com/x"), {}, resolver=lambda h, p: "93.184.216.34",
                verify=False)
    seen = _client_spy(monkeypatch)
    eg.send("GET", eg.parse_target("https://api.example.com/x"), {}, resolver=lambda h, p: "93.184.216.34",
            transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert seen["verify"] is True


def test_the_overall_deadline_stops_a_slow_trickle(monkeypatch):
    import types

    calls = []

    def monotonic():
        calls.append(1)
        return 0.0 if len(calls) == 1 else eg.DEADLINE_S + 1  # the start, then "too late"

    monkeypatch.setattr(eg, "time", types.SimpleNamespace(monotonic=monotonic))

    def handler(request):
        return httpx.Response(200, content=b"slow")

    with pytest.raises(eg.EgressError) as e:
        eg.send("GET", eg.parse_target("https://api.example.com/x"), {}, resolver=lambda h, p: "93.184.216.34",
                transport=httpx.MockTransport(handler))
    assert e.value.code == "timeout"


@pytest.fixture
def tls_server(tmp_path):
    """A real TLS server on 127.0.0.1 with a certificate for good.test, signed by a throwaway CA."""
    import datetime
    import http.server
    import ssl
    import threading

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test ca")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                       crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "good.test")]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(2)
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("good.test")]), critical=False)
            # The fields Python 3.13+'s VERIFY_X509_STRICT requires (key ids, usage), as on any real certificate.
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
    srv.socket = sctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cctx = ssl.create_default_context(cadata=ca.public_bytes(serialization.Encoding.PEM).decode())
    yield srv.server_address[1], cctx
    srv.shutdown()


def test_real_tls_verifies_the_name_sent_as_sni(tls_server, monkeypatch):
    port, ctx = tls_server
    monkeypatch.setattr(eg, "is_public_ip", lambda a: True)  # the test server lives on 127.0.0.1
    resp, body = eg.send("GET", eg.parse_target(f"https://good.test:{port}/x"), {},
                         resolver=lambda h, p: "127.0.0.1", verify=ctx)
    assert resp.status_code == 200 and body == b"ok"
    with pytest.raises(eg.EgressError) as e:
        eg.send("GET", eg.parse_target(f"https://evil.test:{port}/x"), {}, resolver=lambda h, p: "127.0.0.1",
                verify=ctx)
    assert e.value.code == "unreachable"


def test_regression_a_key_echoed_as_json_with_raw_non_ascii_is_masked_whole():
    # Before: only the ASCII-escaped JSON form (é) was a mask form, so a server that writes JSON with non-ASCII
    # left as-is (JSON.stringify) or with upper-case é echoed a piece of the key back unmasked.
    r = Redactor()
    r.register(VALUE2)
    for form in (json.dumps(VALUE2, ensure_ascii=False)[1:-1], json.dumps(VALUE2)[1:-1].replace("\\u00e9", "\\u00E9")):
        out = r.scrub('{"echo":"' + form + '"}')
        assert out == '{"echo":"' + MASK + '"}', "a piece of the key survived"
