"""Windy Code tools register only where the IDE's Agent Bus is LIVE (10-21: a stale socket file left by an exited
IDE registered them on every agent on the host, and owner tools went 66 -> 71 in CI)."""

from __future__ import annotations

import os
import socket
import sys
import tempfile

import pytest

from windyfly.tools import windycode as wc

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets")


@pytest.fixture
def sock_path(monkeypatch):
    d = tempfile.mkdtemp(prefix="wc-")          # short path: AF_UNIX paths are capped near 108 bytes
    path = os.path.join(d, "bus.sock")
    monkeypatch.delenv("WINDYCODE_AGENT_SOCK", raising=False)
    monkeypatch.setattr(wc, "_socket_path", lambda: path)
    yield path
    if os.path.exists(path):
        os.unlink(path)
    os.rmdir(d)


def test_a_listening_socket_counts(sock_path):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    try:
        assert wc.available() is True
    finally:
        srv.close()


def test_a_leftover_socket_file_with_nobody_listening_does_not(sock_path):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.close()                                 # the IDE exited; the file stays
    assert os.path.exists(sock_path) and wc.available() is False


def test_a_plain_file_or_nothing_does_not(sock_path):
    assert wc.available() is False
    open(sock_path, "w").close()
    assert wc.available() is False


def test_the_env_setting_still_configures_it(sock_path, monkeypatch):
    monkeypatch.setenv("WINDYCODE_AGENT_SOCK", sock_path)
    assert wc.available() is True


def test_a_live_socket_owned_by_another_user_does_not(sock_path, monkeypatch):
    """The default path is in a shared /tmp: another user's IDE must never be driven by this agent."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    real_stat = os.stat

    class _Other:
        def __init__(self, st):
            self.st_mode, self.st_uid = st.st_mode, os.getuid() + 1

    monkeypatch.setattr(wc.os, "stat", lambda p, *a, **k: _Other(real_stat(p)) if p == sock_path else real_stat(p, *a, **k))
    try:
        assert wc.available() is False
    finally:
        srv.close()


def test_every_connect_checks_the_owner_too(sock_path, monkeypatch):
    """available() runs at boot; a socket another user puts at the same path later is refused at the connect."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    real_stat = os.stat

    class _Other:
        def __init__(self, st):
            self.st_mode, self.st_uid = st.st_mode, os.getuid() + 1

    monkeypatch.setattr(wc.os, "stat", lambda p, *a, **k: _Other(real_stat(p)) if p == sock_path else real_stat(p, *a, **k))
    try:
        with pytest.raises(wc.WindyCodeUnavailable, match="doesn't belong to this user"):
            with wc._BusConnection():
                pass
    finally:
        srv.close()


def test_no_socket_at_connect_is_unavailable(sock_path):
    with pytest.raises(wc.WindyCodeUnavailable, match="doesn't appear to be running"):
        with wc._BusConnection():
            pass
