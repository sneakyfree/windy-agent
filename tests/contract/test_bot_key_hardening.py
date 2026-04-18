"""Contract tests for the hardened wk_ bot-key flow.

Covers:
- Mint accepts a requested scopes list; server-granted scopes become
  authoritative (downscope behaviour).
- BotCredential.has_scope matches exact, wildcard, and family wildcards.
- Revoke hits /api/v1/identity/bot-keys/revoke and fans out cascade
  webhooks to platforms.
- Local cache is cleared when the revoked key_id matches it.
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


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_credentials, "_CACHE_FILE", tmp_path / "bot_key.json")
    monkeypatch.setattr(audit, "AUDIT_LOG_PATH", tmp_path / "audit" / "bot_key_usage.jsonl")
    monkeypatch.setenv("WINDY_PRO_URL", PRO_BASE)
    monkeypatch.setenv("WINDYFLY_AUDIT_LOG", str(tmp_path / "audit" / "bot_key_usage.jsonl"))
    clear_cached_bot_key()
    yield
    clear_cached_bot_key()


class TestScopedMint:
    @respx.mock
    async def test_mint_sends_requested_scopes(self):
        route = respx.post(f"{PRO_BASE}/api/v1/identity/bot-keys/mint").mock(
            return_value=httpx.Response(200, json={
                "bot_key": "wk_scoped",
                "expires_at": "2027-04-16T00:00:00Z",
                "key_id": "wbk_1",
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
        respx.post(f"{PRO_BASE}/api/v1/identity/bot-keys/mint").mock(
            return_value=httpx.Response(200, json={
                "bot_key": "wk_down",
                "expires_at": "2027-04-16T00:00:00Z",
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
    @respx.mock
    async def test_revoke_posts_key_id_and_reason(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", "owner_jwt")
        route = respx.post(f"{PRO_BASE}/api/v1/identity/bot-keys/revoke").mock(
            return_value=httpx.Response(200, json={"revoked": True})
        )

        summary = await revoke_bot_key(key_id="wbk_42", reason="compromised")

        body = json.loads(route.calls.last.request.content)
        assert body == {"key_id": "wbk_42", "reason": "compromised"}
        assert summary["revoked"] is True

    @respx.mock
    async def test_revoke_cascades_to_platform_webhooks(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", "j")
        respx.post(f"{PRO_BASE}/api/v1/identity/bot-keys/revoke").mock(
            return_value=httpx.Response(200)
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
        respx.post(f"{PRO_BASE}/api/v1/identity/bot-keys/revoke").mock(
            return_value=httpx.Response(200)
        )

        await revoke_bot_key(key_id="wbk_42", reason="x")

        assert bot_credentials._load_cached() is None

    @respx.mock
    async def test_revoke_keeps_unrelated_cache(self, monkeypatch):
        monkeypatch.setenv("WINDY_JWT", "j")
        bot_credentials._save_cached(BotCredential(
            bot_key="wk_current",
            expires_at=datetime.now(timezone.utc) + timedelta(days=100),
            key_id="wbk_other",
        ))
        respx.post(f"{PRO_BASE}/api/v1/identity/bot-keys/revoke").mock(
            return_value=httpx.Response(200)
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
        lines = path.read_text().strip().splitlines()
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

        rec = json.loads((tmp_path / "audit" / "bot_key_usage.jsonl").read_text().strip())
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

        rec = json.loads((tmp_path / "audit" / "bot_key_usage.jsonl").read_text().strip())
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
        respx.post(f"{PRO_BASE}/api/v1/identity/bot-keys/mint").mock(
            return_value=httpx.Response(200, json={
                "bot_key": "wk_new",
                "expires_at": "2027-04-16T00:00:00Z",
                "key_id": "wbk_new",
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
