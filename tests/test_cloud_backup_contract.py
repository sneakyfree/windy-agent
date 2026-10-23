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



def _real_db(path, rows=1):
    """A real SQLite file: backups go through SQLite's backup API now, so
    fake bytes with a SQLite header are no longer enough."""
    import sqlite3
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE t (i INTEGER PRIMARY KEY, v TEXT)")
    c.executemany("INSERT INTO t VALUES (?, ?)", [(i, "the real database") for i in range(rows)])
    c.commit()
    c.close()


def _rows(path):
    import sqlite3
    c = sqlite3.connect(str(path))
    try:
        return c.execute("SELECT i, v FROM t ORDER BY i").fetchall()
    finally:
        c.close()

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
        monkeypatch.setenv("WINDY_BACKUP_KEY", "contract-test-owner-key")  # uploads need an owner-held key (Boss 10-17)
        self._mocks(monkeypatch)
        db = tmp_path / "data" / "windyfly.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        _real_db(db)

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
        assert meta["key"] == "owner"   # Cloud can tell the key: only an owner-held key uploads (since #573)
        assert out["backup_id"].startswith("windyfly-") and out["backup_id"].endswith(".enc")


class TestBackupAndListRoundTrip:
    def test_backup_then_list(self, monkeypatch, tmp_path):
        """The upload and list halves; the restore itself is tests/test_backup_restore.py (asks first)."""
        monkeypatch.setenv("WINDY_BACKUP_KEY", "contract-test-owner-key")  # uploads need an owner-held key (Boss 10-17)
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
        _real_db(db, rows=50)

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
        # exact round-trip through R2 + AES-GCM: what was stored decrypts to the same rows
        (name,) = store
        out = tmp_path / "decrypted.db"
        out.write_bytes(cb._maybe_decompress(cb._decrypt_data(store[name], cb._get_encryption_key())))
        assert _rows(out) == [(i, "the real database") for i in range(50)]


class TestErrorSurfacing:
    """The scheduled backup on Windy 0 failed with a blank reason —
    'Scheduled backup failed: ' with nothing after it — because some
    exceptions stringify to ''. A backup that fails with no reason is
    undiagnosable. Every failure must carry a non-empty, typed reason."""

    def test_describe_error_never_empty(self):
        # A bare exception with no message stringifies to "" — the exact
        # trap that hid the real failure. Must still name the type.
        class _Blank(Exception):
            pass

        assert cb._describe_error(_Blank()) == "_Blank"
        assert cb._describe_error(ValueError("boom")) == "ValueError: boom"
        # whitespace-only message collapses to just the type
        assert cb._describe_error(RuntimeError("   ")) == "RuntimeError"

    def test_backup_upload_returns_typed_error_on_blank_exception(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_BACKUP_KEY", "contract-test-owner-key")  # uploads need an owner-held key (Boss 10-17)
        # The realistic reproduction of the Windy 0 blank failure: the
        # upload's generic `except Exception as e` caught something whose
        # str() was "" → returned {"error": ""} → main.py logged
        # "Scheduled backup failed: " (blank). Drive a message-less
        # exception from the httpx call and assert the returned error is
        # non-empty and names the type.
        class _Silent(Exception):
            pass  # str(_Silent()) == ""

        db = tmp_path / "data" / "windyfly.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        _real_db(db)
        monkeypatch.setenv("WINDY_JWT", "tok")  # auth-header fallback

        import windyfly.trust.gate as gate
        monkeypatch.setattr(gate, "require_trust", AsyncMock(), raising=False)

        def _raise_client(*a, **k):
            raise _Silent()  # inside the upload try → hits the generic except

        monkeypatch.setattr("httpx.AsyncClient", _raise_client)

        res = _run(cb.backup_to_cloud({"memory": {"db_path": str(db)}}))
        assert res["success"] is False
        assert res["error"], "error must never be empty (the Windy 0 bug)"
        assert "_Silent" in res["error"]


class TestCompression:
    """A 122 MB SQLite DB on Windy 0 gzipped to 28 MB (~4.3x). Shipping
    the raw DB over a ~270 KB/s uplink is what blew the write timeout;
    compress-then-encrypt is the fix. Restore must transparently handle
    both compressed backups and older raw ones."""

    def test_maybe_decompress_gzip_and_passthrough(self):
        import gzip
        raw = b"SQLite format 3\x00" + b"payload" * 1000
        assert cb._maybe_decompress(gzip.compress(raw)) == raw
        # A non-gzip (legacy raw SQLite) payload passes through untouched.
        assert cb._maybe_decompress(raw) == raw

    def test_compress_then_encrypt_round_trips(self):
        import gzip
        key = cb._get_encryption_key()
        db_bytes = b"SQLite format 3\x00" + (b"repetitive" * 5000)
        # backup does: gzip -> encrypt ; restore does: decrypt -> maybe_decompress
        stored = cb._encrypt_data(gzip.compress(db_bytes), key)
        restored = cb._maybe_decompress(cb._decrypt_data(stored, key))
        assert restored == db_bytes
        # and it actually shrank (highly compressible fixture)
        assert len(gzip.compress(db_bytes)) < len(db_bytes)

    def test_upload_timeout_is_generous_for_writes(self):
        # A bulk upload must not use a 60s flat timeout (the WriteTimeout
        # trap). Write phase gets real headroom; connect stays tight.
        import httpx
        assert isinstance(cb._TIMEOUT, httpx.Timeout)
        assert cb._TIMEOUT.write >= 300
        assert cb._TIMEOUT.connect <= 60
