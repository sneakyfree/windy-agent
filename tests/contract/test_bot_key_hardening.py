"""Contract tests for the hardened wk_ bot-key flow.

Covers:
- Mint accepts a requested scopes list; server-granted scopes become
  authoritative (downscope behaviour). The route is windy-pro's real
  POST /api/v1/identity/api-keys.
- BotCredential.has_scope matches exact, wildcard, and family wildcards.
- Revoke hits the real DELETE /api/v1/identity/api-keys/<key id>,
  treats ONLY a server-confirmed {"revoked": true} as success, and fans
  out cascade webhooks to platforms.
- Local cache is cleared when the revoked key_id matches it — and KEPT
  when the revocation was not confirmed.
- Audit log is written every time a wk_ key is used.
- rotate_on_trust_change re-mints using the cached passport.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from windyfly.auth import audit, bot_credentials
from windyfly.auth.audit import audit_bot_key_call, log_bot_key_use
from windyfly.auth.bot_credentials import (
    BotCredential,
    clear_cached_bot_key,
    mint_bot_key,
    revoke_bot_key,
    rotate_on_trust_change,
)

PRO_BASE = "https://pro.windy.test"
MINT_URL = f"{PRO_BASE}/api/v1/identity/api-keys"
REVOKE_URL = f"{PRO_BASE}/api/v1/identity/api-keys"   # + /<key id>
BOT_ID = "bot_identity_9f2c"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_credentials, "_CACHE_FILE", tmp_path / "bot_key.json")
    monkeypatch.setattr(audit, "AUDIT_LOG_PATH", tmp_path / "audit" / "bot_key_usage.jsonl")
    monkeypatch.setenv("WINDY_PRO_URL", PRO_BASE)
    monkeypatch.setenv("WINDYFLY_AUDIT_LOG", str(tmp_path / "audit" / "bot_key_usage.jsonl"))
    monkeypatch.setenv("BOT_IDENTITY_ID", BOT_ID)
    clear_cached_bot_key()
    yield
    clear_cached_bot_key()


class TestScopedMint:
    @respx.mock
    async def test_mint_sends_requested_scopes(self):
        route = respx.post(MINT_URL).mock(
            return_value=httpx.Response(201, json={
                "apiKey": "wk_scoped",
                "keyPrefix": "wk_scoped_x",
                "expiresAt": "2027-04-16T00:00:00Z",
                "id": "wbk_1",
                "scopes": ["mail:send", "cloud:upload"],
            })
        )

        cred = await mint_bot_key(
            owner_jwt="j",
            passport_number="ET-1",
            scopes=["mail:send", "cloud:upload"],
        )

        body = json.loads(route.calls.last.request.content)
        assert body["scopes"] == ["mail:send", "cloud:upload"]
        assert cred.scopes == ["mail:send", "cloud:upload"]
        assert cred.key_id == "wbk_1"

    @respx.mock
    async def test_server_downscope_is_authoritative(self):
        respx.post(MINT_URL).mock(
            return_value=httpx.Response(201, json={
                "apiKey": "wk_down",
                "expiresAt": "2027-04-16T00:00:00Z",
                "scopes": ["chat:read"],
            })
        )

        cred = await mint_bot_key(
            owner_jwt="j",
            passport_number="ET-1",
            scopes=["mail:send", "chat:read", "cloud:upload"],
        )

        assert cred.scopes == ["chat:read"]
        assert cred.has_scope("chat:read")
        assert not cred.has_scope("mail:send")


class TestScopeMatching:
    def _cred(self, scopes: list[str]) -> BotCredential:
        return BotCredential(
            bot_key="k",
            expires_at=datetime.now(timezone.utc) + timedelta(days=180),
            scopes=scopes,
        )

    def test_exact_match(self):
        assert self._cred(["mail:send"]).has_scope("mail:send")

    def test_family_wildcard(self):
        assert self._cred(["mail:*"]).has_scope("mail:send")
        assert not self._cred(["mail:*"]).has_scope("chat:read")

    def test_global_wildcard(self):
        assert self._cred(["*"]).has_scope("anything:goes")

    def test_no_match(self):
        assert not self._cred(["chat:read"]).has_scope("mail:send")


class TestRevoke:
    """Revocation is the highest-stakes call in this module: a wk_ key is
    good for 365 days, so a revoke that quietly does nothing leaves a
    live credential nobody is watching. Every test here exists to stop a
    false success."""

    @respx.mock
    async def test_revoke_deletes_by_key_id(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", "owner_jwt")
        route = respx.delete(f"{REVOKE_URL}/wbk_42").mock(
            return_value=httpx.Response(200, json={"revoked": True})
        )

        summary = await revoke_bot_key(key_id="wbk_42", reason="compromised")

        assert route.called
        # The key id travels in the PATH; a DELETE carries no body.
        assert route.calls.last.request.url.path.endswith("/api/v1/identity/api-keys/wbk_42")
        assert route.calls.last.request.headers["Authorization"] == "Bearer owner_jwt"
        assert not route.calls.last.request.content
        assert summary["revoked"] is True
        assert summary["status"] == "revoked"
        assert "compromised" in summary["detail"]

    @respx.mock
    async def test_key_id_is_url_escaped(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", "j")
        route = respx.delete(url__regex=rf"{PRO_BASE}/api/v1/identity/api-keys/.*").mock(
            return_value=httpx.Response(200, json={"revoked": True})
        )

        await revoke_bot_key(key_id="wbk/../42", reason="x")

        assert "wbk%2F..%2F42" in str(route.calls.last.request.url)

    @respx.mock
    async def test_revoked_false_is_not_success(self, monkeypatch):
        """The server answers 200 {"revoked": false} for an unknown key
        id. That is the key NOT being revoked."""
        monkeypatch.setenv("WINDY_JWT", "j")
        respx.delete(f"{REVOKE_URL}/wbk_ghost").mock(
            return_value=httpx.Response(200, json={"revoked": False})
        )

        summary = await revoke_bot_key(key_id="wbk_ghost", reason="x")

        assert summary["revoked"] is False
        assert summary["status"] == "not_found"
        assert "NOTHING WAS REVOKED" in summary["detail"]

    @respx.mock
    async def test_revoked_false_logs_a_warning(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("WINDY_JWT", "j")
        respx.delete(f"{REVOKE_URL}/wbk_ghost").mock(
            return_value=httpx.Response(200, json={"revoked": False})
        )

        with caplog.at_level(logging.DEBUG, logger="windyfly.auth.bot_credentials"):
            await revoke_bot_key(key_id="wbk_ghost", reason="x")

        assert any(
            r.levelno >= logging.WARNING and "NOT revoked" in r.getMessage()
            for r in caplog.records
        )

    @respx.mock
    async def test_http_404_is_reported_as_route_missing(self, monkeypatch):
        """A 404 from the route itself — the exact shape of the bug this
        replaced, where every revoke 404'd and reported success."""
        monkeypatch.setenv("WINDY_JWT", "j")
        respx.delete(f"{REVOKE_URL}/wbk_42").mock(return_value=httpx.Response(404, text="Not Found"))

        summary = await revoke_bot_key(key_id="wbk_42", reason="x")

        assert summary["revoked"] is False
        assert summary["status"] == "http_404"
        assert "NOTHING WAS REVOKED" in summary["detail"]

    @respx.mock
    async def test_500_is_not_success(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", "j")
        respx.delete(f"{REVOKE_URL}/wbk_42").mock(return_value=httpx.Response(500, text="boom"))

        summary = await revoke_bot_key(key_id="wbk_42", reason="x")

        assert summary["revoked"] is False
        assert summary["status"] == "http_500"

    @respx.mock
    async def test_unreadable_2xx_body_is_unconfirmed_not_success(self, monkeypatch):
        """A 200 that isn't the documented JSON proves nothing. The old
        code called any 2xx a revocation."""
        monkeypatch.setenv("WINDY_JWT", "j")
        respx.delete(f"{REVOKE_URL}/wbk_42").mock(
            return_value=httpx.Response(200, text="<html>proxy says hi</html>")
        )

        summary = await revoke_bot_key(key_id="wbk_42", reason="x")

        assert summary["revoked"] is False
        assert summary["status"] == "unconfirmed"

    @respx.mock
    async def test_missing_key_id_sends_no_request(self, monkeypatch):
        """A credential cached without a key id cannot be revoked. Say
        so — do not fire a request at /api-keys/ and call it done."""
        monkeypatch.setenv("WINDY_JWT", "j")
        route = respx.delete(url__regex=rf"{PRO_BASE}/api/v1/identity/api-keys.*").mock(
            return_value=httpx.Response(200, json={"revoked": True})
        )

        with pytest.raises(RuntimeError, match="key_id required"):
            await revoke_bot_key(key_id="", reason="x")

        assert not route.called

    @respx.mock
    async def test_revoke_cascades_to_platform_webhooks(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", "j")
        respx.delete(f"{REVOKE_URL}/wbk_42").mock(
            return_value=httpx.Response(200, json={"revoked": True})
        )
        hook_a = respx.post("https://cloud.windy.test/webhooks/auth").mock(
            return_value=httpx.Response(200)
        )
        hook_b = respx.post("https://mail.windy.test/webhooks/auth").mock(
            return_value=httpx.Response(202)
        )

        summary = await revoke_bot_key(
            key_id="wbk_42",
            reason="rotation",
            cascade_webhook_urls=[
                "https://cloud.windy.test/webhooks/auth",
                "https://mail.windy.test/webhooks/auth",
            ],
        )

        assert hook_a.called and hook_b.called
        assert summary["cascade"]["https://cloud.windy.test/webhooks/auth"] == 200
        assert summary["cascade"]["https://mail.windy.test/webhooks/auth"] == 202
        # Payload carries the event name.
        hook_body = json.loads(hook_a.calls.last.request.content)
        assert hook_body["event"] == "bot_key.revoked"
        assert hook_body["key_id"] == "wbk_42"

    @respx.mock
    async def test_revoke_clears_matching_local_cache(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", "j")
        bot_credentials._save_cached(BotCredential(
            bot_key="wk_current",
            expires_at=datetime.now(timezone.utc) + timedelta(days=100),
            key_id="wbk_42",
        ))
        respx.delete(f"{REVOKE_URL}/wbk_42").mock(
            return_value=httpx.Response(200, json={"revoked": True})
        )

        summary = await revoke_bot_key(key_id="wbk_42", reason="x")

        assert summary["cache_cleared"] is True
        assert bot_credentials._load_cached() is None

    @respx.mock
    async def test_unconfirmed_revoke_keeps_the_cache(self, monkeypatch):
        """Dropping the cache on a failed revoke would discard the only
        handle on a key that is still live — we could never retry."""
        monkeypatch.setenv("WINDY_JWT", "j")
        bot_credentials._save_cached(BotCredential(
            bot_key="wk_current",
            expires_at=datetime.now(timezone.utc) + timedelta(days=100),
            key_id="wbk_42",
        ))
        respx.delete(f"{REVOKE_URL}/wbk_42").mock(return_value=httpx.Response(404))

        summary = await revoke_bot_key(key_id="wbk_42", reason="x")

        assert summary["cache_cleared"] is False
        still = bot_credentials._load_cached()
        assert still is not None and still.key_id == "wbk_42"

    @respx.mock
    async def test_revoke_keeps_unrelated_cache(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", "j")
        bot_credentials._save_cached(BotCredential(
            bot_key="wk_current",
            expires_at=datetime.now(timezone.utc) + timedelta(days=100),
            key_id="wbk_other",
        ))
        respx.delete(f"{REVOKE_URL}/wbk_42").mock(
            return_value=httpx.Response(200, json={"revoked": True})
        )

        await revoke_bot_key(key_id="wbk_42", reason="x")

        assert bot_credentials._load_cached() is not None


class TestAuditLog:
    def test_log_bot_key_use_appends_json_record(self, tmp_path):
        log_bot_key_use(
            key_id="wbk_1",
            scope_used="cloud:upload",
            target_url="https://cloud.windy.test/api/v1/archive/agent",
            response_status=201,
            latency_ms=123.4,
        )
        path = tmp_path / "audit" / "bot_key_usage.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["key_id"] == "wbk_1"
        assert rec["scope_used"] == "cloud:upload"
        assert rec["response_status"] == 201
        assert rec["latency_ms"] == 123.4
        assert rec["target_url"].endswith("/api/v1/archive/agent")
        assert "timestamp" in rec

    async def test_context_manager_records_latency_and_status(self, tmp_path):
        with audit_bot_key_call(
            key_id="wbk_x",
            scope_used="mail:send",
            target_url="https://mail.test/send",
        ) as ctx:
            ctx["response_status"] = 200

        rec = json.loads((tmp_path / "audit" / "bot_key_usage.jsonl").read_text(encoding="utf-8").strip())
        assert rec["response_status"] == 200
        assert rec["latency_ms"] >= 0

    async def test_context_manager_records_even_on_exception(self, tmp_path):
        with pytest.raises(RuntimeError):
            with audit_bot_key_call(
                key_id="wbk_x",
                scope_used="cloud:upload",
                target_url="https://cloud.test/upload",
            ) as ctx:
                ctx["response_status"] = 500
                raise RuntimeError("network borked")

        rec = json.loads((tmp_path / "audit" / "bot_key_usage.jsonl").read_text(encoding="utf-8").strip())
        assert rec["response_status"] == 500


class TestTrustRotation:
    @respx.mock
    async def test_rotate_on_trust_change_re_mints(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", "owner_jwt")
        bot_credentials._save_cached(BotCredential(
            bot_key="wk_old",
            expires_at=datetime.now(timezone.utc) + timedelta(days=60),
            passport_number="ET-7",
            scopes=["mail:send"],
        ))
        respx.post(MINT_URL).mock(
            return_value=httpx.Response(201, json={
                "apiKey": "wk_new",
                "expiresAt": "2027-04-16T00:00:00Z",
                "id": "wbk_new",
                "scopes": ["mail:send", "cloud:upload"],
            })
        )

        new_cred = await rotate_on_trust_change(new_band="stable")

        assert new_cred is not None
        assert new_cred.bot_key == "wk_new"
        assert new_cred.scopes == ["mail:send", "cloud:upload"]

    async def test_rotate_skips_without_jwt(self, monkeypatch):
        monkeypatch.delenv("WINDY_JWT", raising=False)
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
        clear_cached_bot_key()

        result = await rotate_on_trust_change(new_band="watch")

        assert result is None
