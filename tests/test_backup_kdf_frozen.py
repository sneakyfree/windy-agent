"""The passport-derived backup key must never change (it decrypts existing backups).

Known answers were computed from the pre-freeze code. If this fails, someone changed
the KDF inputs (or the 'Windy Fly' default name): existing backups would stop
decrypting. Do not 'fix' the test; fix the change.
"""
from windyfly import cloud_backup as cb

KNOWN = {
    ("windyfly-local", "Windy Fly"): "ad7fb8eb62ef92755bf0beda373fa23b72cc6010aa202b7dde305632f6cfd899",
    ("ET26-TEST-ABCD", "Windy Fly"): "680062c73a7abbd3719fa4b42a0ae5f1e30a254355cca81805e1c1726aa9b249",
}


def _key(monkeypatch, passport=None, name=None):
    monkeypatch.delenv("WINDY_BACKUP_KEY", raising=False)
    for k, v in (("ETERNITAS_PASSPORT", passport), ("WINDYFLY_AGENT_NAME", name)):
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    return cb._get_encryption_key().hex()


def test_default_name_is_frozen():
    assert cb._KDF_DEFAULT_AGENT_NAME == "Windy Fly"


def test_known_answers_unchanged(monkeypatch):
    assert _key(monkeypatch) == KNOWN[("windyfly-local", "Windy Fly")]
    assert _key(monkeypatch, passport="ET26-TEST-ABCD") == KNOWN[("ET26-TEST-ABCD", "Windy Fly")]


def test_branding_does_not_leak_into_the_key(monkeypatch):
    from windyfly import branding

    monkeypatch.setattr(branding, "BRAND_NAME", "Windy Fly Agent", raising=False)
    assert _key(monkeypatch) == KNOWN[("windyfly-local", "Windy Fly")]
