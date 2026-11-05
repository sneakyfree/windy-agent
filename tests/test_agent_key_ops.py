"""Key-ops v1 (eternitas #302, docs/spec/agent-key-ops.v1.md): a key-only agent rotates with its OWN active key.

DARK behind WINDY_AGENT_KEY_OPS=1. Pinned here:
- off: unchanged (a key-only agent with no passport token still gets needs_login)
- on: the new key is registered with an Eternitas-Agent-Proof signed by the OLD key, no bearer on any call; the
  body proof is the NEW key's own over a SECOND nonce; then the old key retires ITSELF with its own proof
- each proof names POST + the route PATH and spends a fresh challenge
- a refused register leaves the old key active and adds nothing; 409 too_many_active_keys keeps the reset hint
- a retire Eternitas refuses (409 last_active_key, 503) keeps the key as ``retiring``; the daily job finishes a
  pending retire with the key's own proof, no token
"""

from __future__ import annotations

import base64
import json
import re
import time

import httpx
import pytest

from windyfly.eternitas import agent_keys as ak
from tests.test_agent_keys import EPT, PASSPORT, FakeEternitas, _err, env  # noqa: F401  (fixture)


def _part(jws: str, i: int) -> dict:
    s = jws.split(".")[i]
    return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))


class FakeKeyOps(FakeEternitas):
    """FakeEternitas + the key-ops v1 agent-key path on register / retire / revoke."""

    def __init__(self) -> None:
        super().__init__()
        self.ops: list[dict] = []  # one per agent-key operation: verb, signer kid, route kid, header nonce
        self.retire_answer: tuple[int, str] | None = None

    def handle(self, req: httpx.Request) -> httpx.Response:
        proof = req.headers.get("eternitas-agent-proof")
        m = re.fullmatch(r"/api/v1/bots/([^/]+)/keys(?:/([^/]+)/(retire|revoke))?", req.url.path)
        if not proof or not m or req.method != "POST":
            return super().handle(req)
        self.calls.append((req.method, req.url.path, req.headers.get("authorization", "")))
        assert "authorization" not in req.headers, "the agent-key path sends no bearer"
        signer = _part(proof, 0).get("kid", "")
        k = self.keys.get(signer)
        if k is None or k["status"] != "active":
            return _err(401, "invalid_agent_proof", "kid is not an active key")
        verified = ak.verify_jws(proof, k["jwk"])
        if verified is None:
            return _err(401, "invalid_agent_proof", "bad signature")
        c = json.loads(verified["payload"])
        if (c["passport"] != PASSPORT or c["htm"] != "POST" or c["htu"] != req.url.path
                or abs(c["iat"] - time.time()) > 300 or c["nonce"] not in self.nonces):
            return _err(401, "invalid_agent_proof", "claims")
        self.nonces.discard(c["nonce"])  # spent BEFORE the operation runs (spec)
        _, route_kid, verb = m.groups()
        self.ops.append({"verb": verb or "register", "signer": signer, "kid": route_kid, "nonce": c["nonce"]})
        if verb is None:
            return self._register_by_key(json.loads(req.content or b"{}"), c["nonce"])
        if route_kid != signer:
            return _err(403, "not_this_key", "a key may only remove itself")
        if self.retire_answer:
            return _err(*self.retire_answer, "refused")
        if sum(v["status"] == "active" for v in self.keys.values()) <= 1:
            return _err(409, "last_active_key", "the last active key needs the owner")
        self.keys[route_kid]["status"] = "retired" if verb == "retire" else "revoked"
        return httpx.Response(200, json={"kid": route_kid, "status": self.keys[route_kid]["status"]})

    def _register_by_key(self, body: dict, header_nonce: str) -> httpx.Response:
        assert set(body) == {"jwk", "custody", "proof"} and body["custody"] == "agent"
        jwk = body["jwk"]
        v = ak.verify_jws(body["proof"], jwk)
        assert v is not None, "the body proof must verify against the NEW key"
        nonce = json.loads(v["payload"])["nonce"]
        assert nonce != header_nonce, "two challenges: one for the header, one for the body"
        if nonce not in self.nonces:
            return _err(400, "nonce_invalid", "nonce")
        if sum(k["status"] == "active" for k in self.keys.values()) >= 2:
            return _err(409, "too_many_active_keys", "retire or revoke one first")
        self.nonces.discard(nonce)
        kid = ak.thumbprint(jwk)
        self.keys[kid] = {"jwk": {x: jwk[x] for x in ("kty", "crv", "x", "y")}, "status": "active"}
        self.registered_via[kid] = "agent_key"
        return httpx.Response(201, json={"kid": kid, "status": "active", "registered_via": "agent_key"})


@pytest.fixture
def key_only(env, monkeypatch):  # noqa: F811
    """A registered agent that then holds NO passport token (a key-only runtime)."""
    fake = FakeKeyOps()
    old = ak.ensure_registered(transport=fake.transport())["kid"]
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN")
    return fake, old


def _local(env_path) -> list[tuple[str, str]]:
    return [(k["kid"], k["status"]) for k in json.loads(env_path.read_text())["eternitas"]["keys"]]


def test_off_by_default_a_key_only_agent_still_needs_login(key_only, monkeypatch):
    fake, old = key_only
    monkeypatch.delenv("WINDY_AGENT_KEY_OPS", raising=False)
    assert ak.rotate(transport=fake.transport())["status"] == "needs_login"
    assert fake.ops == [] and fake.keys[old]["status"] == "active"


def test_on_rotates_with_the_old_key_and_no_token(key_only, env, monkeypatch):  # noqa: F811
    fake, old = key_only
    monkeypatch.setenv("WINDY_AGENT_KEY_OPS", "1")
    calls_before = len(fake.calls)
    r = ak.rotate(transport=fake.transport())
    assert r["status"] == "rotated" and r["old_kid"] == old and r["via"] == "agent_key"
    new = r["kid"]
    assert [(o["verb"], o["signer"]) for o in fake.ops] == [("register", old), ("retire", old)]
    assert fake.ops[1]["kid"] == old  # the old key retired ITSELF
    assert fake.registered_via[new] == "agent_key"
    assert fake.keys[old]["status"] == "retired" and fake.keys[new]["status"] == "active"
    assert all(bearer == "" for _, _, bearer in fake.calls[calls_before:]), "no token on any call"
    assert _local(env) == [(new, "active")]
    assert json.loads(env.read_text())["eternitas"]["kid"] == new


def test_each_proof_spends_a_fresh_challenge(key_only, monkeypatch):
    fake, _ = key_only
    monkeypatch.setenv("WINDY_AGENT_KEY_OPS", "1")
    before = sum(1 for _, p, _ in fake.calls if p.endswith("/keys/challenge"))
    ak.rotate(transport=fake.transport())
    after = sum(1 for _, p, _ in fake.calls if p.endswith("/keys/challenge"))
    assert after - before == 3  # register header + register body + retire header
    assert len({o["nonce"] for o in fake.ops}) == 2


def test_a_refused_register_keeps_the_old_key_and_adds_nothing(key_only, env, monkeypatch):  # noqa: F811
    fake, old = key_only
    monkeypatch.setenv("WINDY_AGENT_KEY_OPS", "1")
    fake.keys[old]["status"] = "retired"  # Eternitas no longer counts it active -> 401 invalid_agent_proof
    r = ak.rotate(transport=fake.transport())
    assert r["status"] == "failed" and r["code"] == "invalid_agent_proof"
    assert _local(env) == [(old, "active")]


def test_too_many_active_keys_keeps_the_reset_hint(key_only, env, monkeypatch):  # noqa: F811
    fake, _ = key_only
    monkeypatch.setenv("WINDY_AGENT_KEY_OPS", "1")
    fake.keys["someone-elses"] = {"jwk": {}, "status": "active"}
    r = ak.rotate(transport=fake.transport())
    assert r["status"] == "failed" and r["code"] == "too_many_active_keys" and "reset" in r["hint"]
    assert (r["kid"], "pending") in _local(env)  # a 409 is never taken as "Eternitas does not hold it"


@pytest.mark.parametrize("answer", [(409, "last_active_key"), (503, "redis_unavailable"), (403, "not_this_key")])
def test_a_refused_retire_keeps_the_key_as_retiring(key_only, env, monkeypatch, answer):  # noqa: F811
    fake, old = key_only
    monkeypatch.setenv("WINDY_AGENT_KEY_OPS", "1")
    fake.retire_answer = answer
    r = ak.rotate(transport=fake.transport())
    assert r["status"] == "rotated"
    assert _local(env) == [(old, "retiring"), (r["kid"], "active")]
    assert fake.keys[old]["status"] == "active"


def test_the_daily_job_finishes_a_pending_retire_with_the_keys_own_proof(key_only, env, monkeypatch):  # noqa: F811
    fake, old = key_only
    monkeypatch.setenv("WINDY_AGENT_KEY_OPS", "1")
    fake.retire_answer = (503, "redis_unavailable")
    new = ak.rotate(transport=fake.transport())["kid"]
    fake.retire_answer = None
    fake.ops.clear()
    assert ak.ensure_registered(transport=fake.transport())["status"] == "registered"
    assert [(o["verb"], o["signer"], o["kid"]) for o in fake.ops] == [("retire", old, old)]
    assert _local(env) == [(new, "active")] and fake.keys[old]["status"] == "retired"


def test_off_the_token_path_is_unchanged(env):  # noqa: F811
    fake = FakeKeyOps()
    old = ak.ensure_registered(transport=fake.transport())["kid"]
    r = ak.rotate(transport=fake.transport())
    assert r["status"] == "rotated" and r["via"] == "own_ept" and fake.ops == []
    assert fake.keys[old]["status"] == "retired"
    assert any(bearer == EPT for _, p, bearer in fake.calls if p.endswith("/retire"))


# ── lost answers (Hub on #714) ───────────────────────────────────────

def _lose_answer_once(fake: FakeKeyOps, match) -> httpx.MockTransport:
    """Eternitas COMMITS the request, then the answer is lost (ReadTimeout), once."""
    state = {"done": False}

    def handler(req: httpx.Request) -> httpx.Response:
        resp = fake.handle(req)
        if not state["done"] and match(req):
            state["done"] = True
            raise httpx.ReadTimeout("answer lost", request=req)
        return resp
    return httpx.MockTransport(handler)


def _is_register(req: httpx.Request) -> bool:
    return req.method == "POST" and req.url.path.endswith("/keys") and "eternitas-agent-proof" in req.headers


def test_a_lost_register_answer_keeps_the_key_and_the_next_rotate_settles_it(key_only, env,  # noqa: F811
                                                                             monkeypatch):
    fake, old = key_only
    monkeypatch.setenv("WINDY_AGENT_KEY_OPS", "1")
    r1 = ak.rotate(transport=_lose_answer_once(fake, _is_register))
    assert r1["status"] == "unknown"
    pending = r1["kid"]
    assert fake.keys[pending]["status"] == "active"  # Eternitas holds it...
    assert (pending, "pending") in _local(env)        # ...and so do we: never an orphan nobody holds
    registers = sum(1 for o in fake.ops if o["verb"] == "register")
    r2 = ak.rotate(transport=fake.transport())
    assert r2["status"] == "rotated" and r2["kid"] == pending
    assert sum(1 for o in fake.ops if o["verb"] == "register") == registers  # settled by reading, not re-sent
    assert _local(env) == [(pending, "active")] and fake.keys[old]["status"] == "retired"


def test_a_lost_retire_answer_is_still_rotated_and_the_daily_job_settles_it(key_only, env,  # noqa: F811
                                                                            monkeypatch):
    fake, old = key_only
    monkeypatch.setenv("WINDY_AGENT_KEY_OPS", "1")
    r = ak.rotate(transport=_lose_answer_once(fake, lambda q: q.url.path.endswith("/retire")))
    assert r["status"] == "rotated"
    assert fake.keys[old]["status"] == "retired" and (old, "retiring") in _local(env)
    # The old key can no longer sign (401 invalid_agent_proof): the public list says it is retired -> done.
    assert ak.ensure_registered(transport=fake.transport())["status"] == "registered"
    assert _local(env) == [(r["kid"], "active")]


@pytest.mark.parametrize("listed,kept", [("retired", False), ("active", True), ("unreadable", True)])
def test_a_self_retire_answered_401_reads_the_public_list(key_only, env, monkeypatch, listed, kept):  # noqa: F811
    fake, old = key_only
    monkeypatch.setenv("WINDY_AGENT_KEY_OPS", "1")
    fake.retire_answer = (401, "invalid_agent_proof")
    new = ak.rotate(transport=fake.transport())["kid"]
    assert (old, "retiring") in _local(env)
    if listed == "retired":
        fake.keys[old]["status"] = "retired"
    base = fake.handle

    def handler(req: httpx.Request) -> httpx.Response:
        if listed == "unreadable" and req.method == "GET":
            return httpx.Response(503, json={"detail": "down"})
        return base(req)
    ak.ensure_registered(transport=httpx.MockTransport(handler))
    assert ((old, "retiring") in _local(env)) is kept
    assert (new, "active") in _local(env)


def _committed_then(fake: FakeKeyOps, register_status: int, register_code: str, list_down: bool):
    """Eternitas COMMITS the register, then answers register_status; the public list may be down."""
    def handler(req: httpx.Request) -> httpx.Response:
        if list_down and req.method == "GET" and req.url.path.endswith("/keys"):
            return httpx.Response(503, json={"detail": "down"})
        resp = fake.handle(req)
        if _is_register(req):
            return _err(register_status, register_code, "lost")
        return resp
    return httpx.MockTransport(handler)


def test_key_exists_with_the_list_unreadable_is_unknown_and_keeps_the_key(key_only, env, monkeypatch):  # noqa: F811
    fake, old = key_only
    monkeypatch.setenv("WINDY_AGENT_KEY_OPS", "1")
    r = ak.rotate(transport=_committed_then(fake, 409, "key_exists", list_down=True))
    assert r["status"] == "unknown"
    assert fake.keys[r["kid"]]["status"] == "active" and (r["kid"], "pending") in _local(env)


@pytest.mark.parametrize("status", [502, 504, 429, 408])
def test_a_5xx_or_429_after_commit_keeps_the_key_and_the_next_run_settles_it(key_only, env, monkeypatch,  # noqa: F811
                                                                              status):
    fake, old = key_only
    monkeypatch.setenv("WINDY_AGENT_KEY_OPS", "1")
    r1 = ak.rotate(transport=_committed_then(fake, status, "upstream", list_down=False))
    assert r1["status"] == "unknown" and (r1["kid"], "pending") in _local(env)
    registers = sum(1 for o in fake.ops if o["verb"] == "register")
    r2 = ak.rotate(transport=fake.transport())
    assert r2["status"] == "rotated" and r2["kid"] == r1["kid"]
    assert sum(1 for o in fake.ops if o["verb"] == "register") == registers  # no second register
    assert _local(env) == [(r1["kid"], "active")]


def test_a_definite_4xx_refusal_drops_the_pending_key(key_only, env, monkeypatch):  # noqa: F811
    fake, old = key_only
    monkeypatch.setenv("WINDY_AGENT_KEY_OPS", "1")

    def handler(req: httpx.Request) -> httpx.Response:
        if _is_register(req):
            return _err(400, "invalid_agent_proof", "refused")  # NOT committed
        return fake.handle(req)
    r = ak.rotate(transport=httpx.MockTransport(handler))
    assert r["status"] == "failed" and _local(env) == [(old, "active")]
