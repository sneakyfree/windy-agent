import base64

import httpx
import pytest

from windyfly import snapshot_key as sk


AUTH = lambda url: {"Authorization": "DPoP tok", "DPoP": "proof"}  # noqa: E731


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WINDY_SNAPSHOT_KEY", raising=False)
    with pytest.raises(sk.SnapshotKeyError):
        sk.fetch_snapshot_key("a1", "wake")


def test_fetch_ok_and_key_not_in_repr(monkeypatch):
    monkeypatch.setenv("WINDY_SNAPSHOT_KEY", "1")
    raw = bytes(range(32))

    def h(req):
        assert req.url.path == "/api/v1/agents/a1/snapshot-key"
        assert req.headers["authorization"] == "DPoP tok" and req.headers["dpop"] == "proof"
        return httpx.Response(200, json={
            "key_b64": base64.b64encode(raw).decode(), "key_version": 3,
            "expires_in": 60})

    k = sk.fetch_snapshot_key("a1", "sleep", client=_client(h), auth=AUTH)
    assert k.key == raw and k.version == 3
    assert base64.b64encode(raw).decode() not in repr(k)


@pytest.mark.parametrize("status,body", [
    (403, {}), (500, {}),
    (200, {"key_b64": "AAAA", "key_version": 1}),
    (200, {"key_b64": "!!", "key_version": 1}),
])
def test_refusals_and_bad_keys(monkeypatch, status, body):
    monkeypatch.setenv("WINDY_SNAPSHOT_KEY", "1")
    c = _client(lambda r: httpx.Response(status, json=body))
    with pytest.raises(sk.SnapshotKeyError):
        sk.fetch_snapshot_key("a1", "wake", client=c, auth=AUTH)


def test_valid_purpose_required(monkeypatch):
    monkeypatch.setenv("WINDY_SNAPSHOT_KEY", "1")
    with pytest.raises(sk.SnapshotKeyError):
        sk.fetch_snapshot_key("a1", "delete", auth=AUTH)


def test_no_agent_key_is_a_clean_error(monkeypatch):
    monkeypatch.setenv("WINDY_SNAPSHOT_KEY", "1")
    with pytest.raises(sk.SnapshotKeyError):
        sk.fetch_snapshot_key("a1", "wake", client=_client(lambda r: httpx.Response(200)))
