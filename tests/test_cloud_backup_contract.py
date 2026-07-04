"""Cloud backup — canonical archive contract + AES-GCM (2026-07-04).

cloud_backup.py was UNTESTED, which is how its wire contract silently
drifted away from Windy Cloud (client sent JSON to a multipart endpoint;
list/restore hit routes that didn't exist) and its encryption stayed a
homemade XOR. These tests pin: AES-256-GCM round-trip + tamper
detection, multipart upload to /archive/agent, list via
/archive/list/windy_fly, and a full backup→list→restore round-trip.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly import cloud_backup as cb


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-0001")
    monkeypatch.setenv("WINDYFLY_AGENT_NAME", "Testy")
    monkeypatch.delenv("WINDY_BACKUP_KEY", raising=False)
    monkeypatch.setattr(cb, "PROJECT_ROOT", tmp_path)


class TestCrypto:
    def test_aes_gcm_round_trip(self):
        key = cb._get_encryption_key()
        pt = b"windyfly database bytes" * 100
        ct = cb._encrypt_data(pt, key)
        assert ct[: cb._GCM_NONCE_BYTES] != ct[cb._GCM_NONCE_BYTES:]  # nonce prefix
        assert cb._decrypt_data(ct, key) == pt

    def test_fresh_nonce_each_call(self):
        key = cb._get_encryption_key()
        a = cb._encrypt_data(b"same", key)
        b = cb._encrypt_data(b"same", key)
        assert a != b  # different nonce → different ciphertext

    def test_tamper_is_rejected(self):
        key = cb._get_encryption_key()
        ct = bytearray(cb._encrypt_data(b"secret", key))
        ct[-1] ^= 0x01  # flip a tag bit
        with pytest.raises(Exception):
            cb._decrypt_data(bytes(ct), key)

    def test_wrong_key_is_rejected(self, monkeypatch):
        key = cb._get_encryption_key()
        ct = cb._encrypt_data(b"secret", key)
        monkeypatch.setenv("WINDY_BACKUP_KEY", "a-different-user-secret")
        with pytest.raises(Exception):
            cb._decrypt_data(ct, cb._get_encryption_key())

    def test_user_key_is_zero_knowledge(self, monkeypatch):
        # With a user secret set, the key does NOT derive from the passport.
        monkeypatch.setenv("WINDY_BACKUP_KEY", "grandmas-recovery-phrase")
        k1 = cb._get_encryption_key()
        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-DIFFERENT")
        k2 = cb._get_encryption_key()
        assert k1 == k2  # passport change doesn't affect a user-keyed backup


class TestUploadContract:
    def _auth(self):
        return patch.multiple(
            "windyfly.cloud_backup",
            _save_backup_state=MagicMock(),
        )

    def _mocks(self, monkeypatch):
        monkeypatch.setattr(
            "windyfly.auth.bot_credentials.ecosystem_auth_header",
            AsyncMock(return_value={"Authorization": "Bearer t"}),
        )
        monkeypatch.setattr(
            "windyfly.auth.bot_credentials.get_bot_key",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "windyfly.trust.gate.require_trust", AsyncMock(return_value=None),
        )

    def test_upload_is_multipart_not_json(self, monkeypatch, tmp_path):
        self._mocks(monkeypatch)
        db = tmp_path / "data" / "windyfly.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(b"SQLite format 3\x00 fake db bytes")

        captured = {}
        resp = MagicMock(status_code=201)
        resp.json.return_value = {"file_id": "fid-1", "size": 42}
        resp.raise_for_status = MagicMock()

        def _post(url, **kw):
            captured["url"] = url
            captured["kw"] = kw
            return resp

        client = AsyncMock()
        client.post = AsyncMock(side_effect=_post)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=ctx):
            out = _run(cb.backup_to_cloud())

        assert out["success"] is True
        assert captured["url"].endswith("/api/v1/archive/agent")
        # multipart: files= present, json= absent
        assert "files" in captured["kw"] and "json" not in captured["kw"]
        assert "file" in captured["kw"]["files"]
        meta = json.loads(captured["kw"]["data"]["metadata"])
        assert meta["encrypted"] is True and "checksum_sha256" in meta
        assert out["backup_id"].startswith("windyfly-") and out["backup_id"].endswith(".enc")


class TestListAndRestoreRoundTrip:
    def test_backup_list_restore(self, monkeypatch, tmp_path):
        # A fake in-memory "cloud": filename -> stored encrypted bytes.
        store: dict[str, bytes] = {}
        monkeypatch.setattr(
            "windyfly.auth.bot_credentials.ecosystem_auth_header",
            AsyncMock(return_value={"Authorization": "Bearer t"}),
        )
        monkeypatch.setattr(
            "windyfly.auth.bot_credentials.get_bot_key", AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "windyfly.trust.gate.require_trust", AsyncMock(return_value=None),
        )

        db = tmp_path / "data" / "windyfly.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        original = b"SQLite format 3\x00" + b"the real database" * 50
        db.write_bytes(original)

        def _make_client():
            client = AsyncMock()

            async def _post(url, files=None, data=None, headers=None):
                name = data["filename"]
                store[name] = files["file"][1]
                r = MagicMock(status_code=201)
                r.json.return_value = {"file_id": name, "size": len(store[name])}
                r.raise_for_status = MagicMock()
                return r

            async def _get(url, headers=None):
                r = MagicMock(status_code=200)
                if "/list/" in url:
                    r.json.return_value = {
                        "product": "windy_fly", "count": len(store),
                        "files": [
                            {"filename": n, "size_bytes": len(store[n]), "created_at": "2026-07-04T00:00:00Z"}
                            for n in sorted(store, reverse=True)
                        ],
                    }
                else:  # /retrieve/windy_fly/{name}
                    name = url.rsplit("/", 1)[-1]
                    r.content = store[name]
                r.raise_for_status = MagicMock()
                return r

            client.post = AsyncMock(side_effect=_post)
            client.get = AsyncMock(side_effect=_get)
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=client)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        with patch("httpx.AsyncClient", side_effect=lambda *a, **k: _make_client()):
            up = _run(cb.backup_to_cloud())
            assert up["success"] is True
            listed = _run(cb.list_backups())
            assert listed["success"], listed.get("error")
            assert len(listed["backups"]) == 1, listed
            # corrupt the local DB, then restore "latest"
            db.write_bytes(b"corrupted")
            res = _run(cb.restore_from_cloud("latest"))

        assert res["success"] is True
        assert db.read_bytes() == original  # exact round-trip through R2 + AES-GCM
