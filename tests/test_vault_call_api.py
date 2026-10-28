"""vault.call_api (Vault everywhere item 3; docs/VAULT_CALL_API_DESIGN.md rev 4; windy-contracts vault-tools.v1 1.0.1).
The Vault client is stubbed at its functions (shared / lease / release); the web side is the REAL egress.send over an
httpx MockTransport with a pinned public address, so the pinned-IP, cap and decode paths are the shipped ones. The
echo server reflects the key in every encoding it might come back in, for two values (one with a quote, a backslash
and a non-ASCII character, so its JSON, percent and hex forms all differ); no form may reach the model."""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
from urllib.parse import quote

import httpx
import jsonschema
import pytest

from windyfly.agent.capabilities import audit
from windyfly.agent.capabilities import vault as vt
from windyfly.agent.capabilities.descriptor import Band
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.tools import bands
from windyfly.vault import call_api as ca
from windyfly.vault import client as vc
from windyfly.vault import egress

CONTRACT = json.loads((Path(__file__).parent / "contracts" / "agent" / "vault-tools.v1.json").read_text())
SAY = CONTRACT["x-refusals"]["say"]
ITEM = "itm_k425u5lvzblgqmnl"
OTHER = "itm_ku5mm5qg3mkdkqg5"
KEY = "sk_live_51AbCdEfGhIjKlMn0123"
ODD = 'pa"ss\\wörd-0123456789xyz'  # its JSON, percent and hex forms all differ from the raw text
API = "https://api.example.com"
PUBLIC_IP = "93.184.216.34"


def _validate(out: dict) -> None:
    jsonschema.validate(out, {**CONTRACT["x-tools"]["vault.call_api"]["result"], "$defs": CONTRACT["$defs"]})


def _forms(value: str, username: str | None = None) -> list[str]:
    raw = value.encode()
    forms = [value, json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1], quote(value, safe=""),
             raw.hex(), base64.b64encode(raw).decode()]
    if username:
        forms.append(base64.b64encode(f"{username}:{value}".encode()).decode())
    return forms


class Echo:
    """A website that reflects what it got: the Authorization / X-Api-Key header and the body, in every form."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []
        self.reply: httpx.Response | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.reply is not None:
            return self.reply
        got = request.headers.get("authorization") or request.headers.get("x-api-key") or ""
        if got.startswith("Basic "):
            got = base64.b64decode(got[6:]).decode().split(":", 1)[1]
        elif got.startswith("Bearer "):
            got = got[7:]
        line = request.headers.get("authorization") or ""
        echoed = {"raw": got, "json": json.dumps(got), "json_u": json.dumps(got, ensure_ascii=False),
                  "pct": quote(got, safe=""), "hex": got.encode().hex(), "b64": base64.b64encode(got.encode()).decode(),
                  "header_line": line, "header_line_b64": base64.b64encode(line.encode()).decode(),
                  "body": request.content.decode() if request.content else None}
        b64 = base64.b64encode(got.encode()).decode()
        text = json.dumps(echoed, ensure_ascii=False) + "\n" + json.dumps(echoed)  # both JSON styles
        return httpx.Response(200, content=text.encode(), headers={
            "content-type": "application/json", "x-request-id": f"req-{b64}", "set-cookie": f"s={b64}",
            "etag": '"abc"', "server": "echo"})


@pytest.fixture
def lab(monkeypatch, tmp_path):
    monkeypatch.setenv(vc.ENV_FLAG, "1")
    monkeypatch.setenv(vc.ENV_URL, "https://vault.example")
    monkeypatch.setenv(vt.ENV_FLAG, "1")
    monkeypatch.setenv(vt.CALL_API_FLAG, "1")
    monkeypatch.setattr("windyfly.platform.windy_state_dir", lambda: tmp_path)
    state = {"value": KEY, "targets": (API,), "leased": [], "released": [], "lease_error": None,
             "shared": [ITEM]}

    def shared():
        return {"items": [{"grant_id": "grt_x", "item_id": i, "label": "k", "expires_at": None}
                          for i in state["shared"]], "vault": "none"}

    def lease(item_id):
        if state["lease_error"]:
            raise state["lease_error"]
        lid = f"lse_{len(state['leased']) + 1}"
        state["leased"].append(lid)
        return vc.Lease(lease_id=lid, item_id=item_id, value=state["value"], expires_at=1, targets=state["targets"])

    def release(lease_id):
        state["released"].append(lease_id)
        return True

    monkeypatch.setattr(vc, "shared", shared)
    monkeypatch.setattr(vc, "lease", lease)
    monkeypatch.setattr(vc, "release", release)
    echo = Echo()
    real_send = egress.send
    resolved = {"ip": PUBLIC_IP}

    def send(*a, **k):
        k.update(transport=httpx.MockTransport(echo), resolver=lambda host, port: resolved["ip"])
        return real_send(*a, **k)

    monkeypatch.setattr(egress, "send", send)
    r = CapabilityRegistry()
    vt.register_vault_capabilities(r, {})
    state.update(echo=echo, registry=r, resolved=resolved, tmp=tmp_path)
    return state


def _call(lab, **args):
    out = lab["registry"].invoke_sync("vault.call_api", {"item_id": ITEM, "method": "GET", "url": API + "/v1/me",
                                                          "auth": "bearer", **args}, Band.OWNER)
    _validate(out)
    return out


def _refusal(out, code):
    assert out["ok"] is False and out["code"] == code and out["say"] == SAY[code], out


def _all_leases_released(lab):
    assert sorted(lab["released"]) == sorted(lab["leased"])


# ── the happy path and the scrub ─────────────────────────────────────────────


@pytest.mark.parametrize("value,auth,extra", [
    (KEY, "bearer", {}),
    (KEY, "header", {"header_name": "X-Api-Key"}),
    (KEY, "basic", {"username": "grant"}),
    (ODD, "basic", {"username": "grant"}),
])
def test_a_get_runs_and_no_form_of_the_key_reaches_the_model(lab, value, auth, extra):
    lab["value"] = value
    out = _call(lab, auth=auth, **extra)
    assert out["ok"] is True and out["status"] == 200 and out["truncated"] is False
    whole = json.dumps(out, ensure_ascii=False) + json.dumps(out)
    for form in _forms(value, extra.get("username")):
        assert form not in whole, f"a form of the key reached the model ({len(form)} chars)"
    assert "[REDACTED]" in out["body"]
    # Only allow-listed headers, lower-case, scrubbed; set-cookie and server never pass.
    assert set(out["headers"]) <= set(ca.HEADER_ALLOW) and "set-cookie" not in out["headers"]
    assert out["headers"]["x-request-id"] == "req-[REDACTED]"
    _all_leases_released(lab)


def test_the_key_goes_out_the_way_auth_says(lab):
    _call(lab)
    _call(lab, auth="header", header_name="X-Api-Key")
    _call(lab, auth="basic", username="grant")
    bearer, header, basic = lab["echo"].seen
    assert bearer.headers["authorization"] == f"Bearer {KEY}"
    assert header.headers["x-api-key"] == KEY and "authorization" not in header.headers
    assert basic.headers["authorization"] == "Basic " + base64.b64encode(f"grant:{KEY}".encode()).decode()
    # Pinned: the socket goes to the resolved address; Host and SNI carry the name.
    assert bearer.url.host == PUBLIC_IP and bearer.headers["host"] == "api.example.com"
    assert bearer.extensions["sni_hostname"] == "api.example.com"


def test_query_is_sent_verbatim_after_the_url(lab):
    _call(lab, url=API + "/v1/search?a=1", query={"q": "windy%20fly", "page": "2"})
    assert str(lab["echo"].seen[0].url).endswith("/v1/search?a=1&q=windy%20fly&page=2")


def test_a_successful_answer_taints_the_turn(lab):
    with bands.turn_context("hi"):
        assert not bands.turn_marked_tainted()
        _call(lab)
        assert bands.turn_marked_tainted()


def test_a_refusal_does_not_taint_the_turn(lab):
    lab["shared"] = []
    with bands.turn_context("hi"):
        _refusal(_call(lab), "not_shared")
        assert not bands.turn_marked_tainted()


def test_the_model_gets_at_most_64_kib_and_is_told_it_was_cut(lab):
    lab["echo"].reply = httpx.Response(200, content=("é" * 50_000).encode())  # 100 KB of 2-byte characters
    out = _call(lab)
    assert out["truncated"] is True and len(out["body"].encode()) <= ca.TO_MODEL_MAX
    assert out["body"] == "é" * (ca.TO_MODEL_MAX // 2)  # cut on a character boundary


def test_a_gzip_answer_is_decoded_then_scrubbed(lab):
    lab["echo"].reply = httpx.Response(200, content=gzip.compress(f"token={KEY}".encode()),
                                       headers={"content-encoding": "gzip"})
    assert _call(lab)["body"] == "token=[REDACTED]"


# ── refusals, each with the contract's words ─────────────────────────────────


def test_an_item_not_shared_is_refused_before_any_lease(lab):
    lab["shared"] = [OTHER]
    _refusal(_call(lab), "not_shared")
    assert lab["leased"] == [] and lab["echo"].seen == []


@pytest.mark.parametrize("err,code", [
    (vc.VaultError("not_found", 404), "not_shared"),
    (vc.VaultError("needs_owner", 403), "not_shared"),
    (vc.VaultError("grant_expired", 403), "not_shared"),
    (vc.VaultError("forbidden", 403), "not_linked"),
    (vc.VaultError("vault_locked", 423), "vault_locked"),
    (vc.VaultError("budget_exhausted", 429), "rate_limited"),
    (vc.VaultError("vault_unavailable", 503), "unavailable"),
    (vc.VaultError("bad_lease"), "unavailable"),
])
def test_lease_refusals_map_to_the_contracts_codes(lab, err, code):
    lab["lease_error"] = err
    _refusal(_call(lab), code)
    assert lab["echo"].seen == []


@pytest.mark.parametrize("targets,url", [
    ((), API + "/x"),                                  # no targets: fail closed
    (("https://other.example.com",), API + "/x"),
    ((API,), "https://api.example.com:8443/x"),        # same host, other port
    ((API,), "https://evil.api.example.com/x"),        # a subdomain is another origin
    (("https://api.example.com:8443",), API + "/x"),
])
def test_a_website_not_on_the_key_is_refused_and_the_lease_released(lab, targets, url):
    lab["targets"] = targets
    _refusal(_call(lab, url=url), "host_not_allowed")
    assert lab["echo"].seen == []
    _all_leases_released(lab)


@pytest.mark.parametrize("value,auth,extra", [
    ("short-key-123", "bearer", {}),                   # < 16: whole-form masking can't guard it
    ("sk_live_0123456789\r\nX-Evil: 1", "bearer", {}),
    ("sk_live_0123456789\tabcdef", "header", {"header_name": "X-Api-Key"}),
    ("sk_live_0123456789\x7fabcdef", "basic", {"username": "grant"}),
    ("sk_live_0123456789ébcdef", "bearer", {}),        # a header can't carry it as-is
])
def test_an_unusable_key_is_refused_before_anything_is_sent(lab, value, auth, extra):
    lab["value"] = value
    _refusal(_call(lab, auth=auth, **extra), "key_unusable")
    assert lab["echo"].seen == []
    _all_leases_released(lab)


@pytest.mark.parametrize("args", [
    {"url": "http://api.example.com/x"},
    {"url": "https://user@api.example.com/x"},
    {"url": "https://api.example.com/x#frag"},
    {"url": "https://api.example.com/x y"},
    {"method": "TRACE"},
    {"auth": "cookie"},
    {"auth": "header"},                                 # header without header_name
    {"auth": "basic"},                                  # basic without username
    {"header_name": "X-Api-Key"},                       # header_name without auth header
    {"auth": "header", "header_name": "Host"},
    {"auth": "header", "header_name": "Cookie"},
    {"auth": "header", "header_name": "Bad Name"},
    {"body": {"a": 1}},                                 # GET never carries a body
    {"query": {"q": "a b"}},
    {"query": {"q": "a&b=c"}},
    {"query": {"bad key": "1"}},
    {"query": {"q": 1}},
    {"query": "q=1"},
    {"query": {f"k{i}": "1" for i in range(33)}},
    {"method": "POST", "body": float("nan")},
    {"item_id": "lse_1"},
    {"extra": "x"},
])
def test_arguments_outside_the_contract_are_bad_arguments(lab, args):
    _refusal(_call(lab, **args), "bad_arguments")
    assert lab["leased"] == [] and lab["echo"].seen == []


@pytest.mark.parametrize("url", ["https://api.example.com/a/../admin", "https://api.example.com/%2e%2E/admin",
                                 "https://api.example.com/./x"])
def test_dot_segments_are_bad_url(lab, url):
    _refusal(_call(lab, url=url), "bad_url")
    assert lab["leased"] == []


def test_a_body_over_64_kib_is_request_too_large(lab):
    _refusal(_call(lab, method="POST", body={"x": "a" * (64 * 1024)}), "request_too_large")
    assert lab["leased"] == []


@pytest.mark.parametrize("ip", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "::ffff:10.0.0.1", "100.64.0.1"])
def test_a_private_address_is_blocked_and_the_lease_released(lab, ip):
    lab["resolved"]["ip"] = ip
    _refusal(_call(lab), "blocked_address")
    assert lab["echo"].seen == []
    _all_leases_released(lab)


@pytest.mark.parametrize("reply", [
    "connect_error",
    httpx.Response(200, content=b"x" * (egress.MAX_RESPONSE + 1)),
    httpx.Response(200, content=b"x", headers={"content-encoding": "br"}),
])
def test_a_failed_call_is_call_failed_and_the_lease_released(lab, monkeypatch, reply):
    if reply == "connect_error":
        def boom(request):
            raise httpx.ConnectError("refused", request=request)
        monkeypatch.setattr(lab["echo"], "reply", None)
        monkeypatch.setattr(Echo, "__call__", lambda self, request: boom(request))
    else:
        lab["echo"].reply = reply
    _refusal(_call(lab), "call_failed")
    _all_leases_released(lab)


def test_a_redirect_is_returned_not_followed(lab):
    lab["echo"].reply = httpx.Response(302, headers={"location": "https://evil.example/steal"})
    out = _call(lab)
    assert out["ok"] is True and out["status"] == 302 and "location" not in out["headers"]
    assert len(lab["echo"].seen) == 1


def test_the_release_happens_even_when_the_release_is_refused(lab, monkeypatch):
    def refuse(lease_id):
        lab["released"].append(lease_id)
        raise vc.VaultError("unavailable", 503)
    monkeypatch.setattr(vc, "release", refuse)
    assert _call(lab)["ok"] is True
    _all_leases_released(lab)


# ── writes ask the owner ─────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_a_write_asks_the_owner_and_sends_nothing(lab, method):
    out = _call(lab, method=method, url=API + "/v1/items/9", body={"name": "x"} if method != "DELETE" else None)
    assert out["ok"] is False and out["needs_owner_ok"] is True
    assert out["say"] == CONTRACT["$defs"]["NeedsOwnerOk"]["properties"]["say"]["const"]
    assert out["confirm_id"].startswith("cnf_")
    assert lab["echo"].seen == []
    _all_leases_released(lab)
    stored = json.loads((lab["tmp"] / "vault_call_confirms.json").read_text())
    assert set(stored[out["confirm_id"]]) == {"req_hash", "expires_at"}  # a hash, never the request itself


def test_a_write_to_a_website_not_on_the_key_is_refused_not_carded(lab):
    _refusal(_call(lab, method="DELETE", url="https://other.example.com/x"), "host_not_allowed")
    assert not (lab["tmp"] / "vault_call_confirms.json").exists()


def test_a_write_with_an_unusable_key_is_refused_not_carded(lab):
    lab["value"] = "short"
    _refusal(_call(lab, method="POST", body={}), "key_unusable")
    assert not (lab["tmp"] / "vault_call_confirms.json").exists()


def test_the_confirm_is_bound_to_the_exact_request():
    t = egress.parse_target(API + "/v1/items/9")
    base = ca._request_hash(ITEM, "DELETE", t, None)
    assert base == ca._request_hash(ITEM, "DELETE", t, None)
    assert base != ca._request_hash(ITEM, "DELETE", egress.parse_target(API + "/v1/items/8"), None)
    assert base != ca._request_hash(ITEM, "PATCH", t, None)
    assert base != ca._request_hash(OTHER, "DELETE", t, None)
    assert base != ca._request_hash(ITEM, "DELETE", t, b"{}")
    assert base != ca._request_hash(ITEM, "DELETE", egress.parse_target(API + "/v1/items/9?x=1"), None)


# ── the budget ───────────────────────────────────────────────────────────────


def test_thirty_calls_an_hour_per_item_then_too_many_calls(lab, monkeypatch):
    now = [1_000_000.0]
    real = ca.call
    monkeypatch.setattr(ca, "call", lambda **k: real(**k, now=lambda: now[0]))
    for _ in range(ca.BUDGET_PER_HOUR):
        assert _call(lab)["ok"] is True
        now[0] += 10
    out = _call(lab)
    _refusal(out, "too_many_calls")
    assert out["retry_after_s"] == 3600 - 300 + 1  # the oldest call (300 s ago) leaves the hour then
    assert len(lab["leased"]) == ca.BUDGET_PER_HOUR  # the refused call leased nothing
    now[0] += out["retry_after_s"]
    assert _call(lab)["ok"] is True
    lab["shared"] = [ITEM, OTHER]
    assert _call(lab, item_id=OTHER)["ok"] is True  # per item


def test_writes_waiting_for_the_owner_do_not_spend_the_budget(lab, monkeypatch):
    for _ in range(ca.BUDGET_PER_HOUR + 5):
        assert _call(lab, method="DELETE")["needs_owner_ok"] is True
    assert _call(lab)["ok"] is True


# ── the audit row and the switches ───────────────────────────────────────────


def test_the_audit_row_holds_the_method_and_host_only(lab, monkeypatch):
    rows = []
    monkeypatch.setattr(audit, "record_action_start", lambda db, q, **k: rows.append(k))
    monkeypatch.setattr(audit, "record_action_end", lambda db, q, **k: None)
    audit.install_audit_hooks(lab["registry"], db=None, write_queue=None)
    _call(lab, url=API + "/v1/users/secret-path", query={"token": "abc"})
    _call(lab, method="POST", body={"note": "private words"})
    assert [json.loads(r["args_json"]) for r in rows] == [{"method": "GET", "host": "api.example.com"},
                                                          {"method": "POST", "host": "api.example.com"}]


def test_a_call_that_does_not_fit_never_reaches_the_audit_row(lab, monkeypatch):
    rows = []
    monkeypatch.setattr(audit, "record_action_start", lambda db, q, **k: rows.append(k))
    audit.install_audit_hooks(lab["registry"], db=None, write_queue=None)
    _refusal(_call(lab, url="http://api.example.com/x"), "bad_arguments")
    assert rows == []


def test_call_api_needs_its_own_flag_at_registration(lab, monkeypatch):
    monkeypatch.delenv(vt.CALL_API_FLAG)
    r = CapabilityRegistry()
    vt.register_vault_capabilities(r, {})
    assert r.get("vault.list") is not None and r.get("vault.call_api") is None


@pytest.mark.parametrize("flag", [vt.CALL_API_FLAG, vt.ENV_FLAG])
def test_either_flag_off_at_call_time_is_switched_off(lab, monkeypatch, flag):
    monkeypatch.setenv(flag, "0")
    _refusal(_call(lab), "switched_off")
    assert lab["leased"] == []


def test_call_api_is_owner_band_only(lab):
    for band in (Band.TRUSTED, Band.USER):
        with pytest.raises(Exception):
            lab["registry"].invoke_sync("vault.call_api", {"item_id": ITEM, "method": "GET", "url": API,
                                                           "auth": "bearer"}, band)
    assert lab["leased"] == []


def test_the_registration_matches_the_contract(lab):
    cap = lab["registry"].get("vault.call_api")
    spec = CONTRACT["x-tools"]["vault.call_api"]
    assert cap.description == spec["description"] and cap.band_required == Band.OWNER
    assert cap.audit_required and cap.strict_args and cap.tier.name == spec["windyfly_tier"]
    args = CONTRACT["$defs"]["CallApiArgs"]
    assert set(cap.input_schema["properties"]) == set(args["properties"])
    assert cap.input_schema["required"] == args["required"]
    for name in ("url", "header_name", "username"):
        assert cap.input_schema["properties"][name]["pattern"] == args["properties"][name]["pattern"], name
    assert cap.input_schema["properties"]["item_id"]["pattern"] == CONTRACT["$defs"]["ItemId"]["pattern"]
    assert ca.HEADER_ALLOW == tuple(CONTRACT["x-limits"]["header_allowlist"])
    assert ca.FORBIDDEN_HEADERS == set(CONTRACT["x-limits"]["forbidden_header_names"])
    assert ca.BUDGET_PER_HOUR == CONTRACT["x-limits"]["calls_per_item_per_hour"]
    assert ca.TO_MODEL_MAX == CONTRACT["x-limits"]["response_to_model_max_bytes"]
