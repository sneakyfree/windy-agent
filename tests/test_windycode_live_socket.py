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
