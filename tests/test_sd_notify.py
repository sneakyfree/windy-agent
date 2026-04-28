"""Regressions for the systemd notify protocol client.

The watchdog wire-up assumes ``sd_notify`` is a no-op when
``NOTIFY_SOCKET`` is unset (dev mode, tests, ad-hoc invocation) and
silently delivers a datagram when it is. Anything noisier here would
spam every test run with warnings.
"""

from __future__ import annotations

import os
import socket
import threading
from unittest.mock import patch

import pytest

from windyfly.observability.sd_notify import (
    notify_ready,
    notify_stopping,
    notify_watchdog,
    sd_notify,
)


class TestNoSocket:
    def test_unset_returns_false_no_send(self, monkeypatch):
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        assert sd_notify("WATCHDOG=1") is False

    def test_empty_returns_false_no_send(self, monkeypatch):
        monkeypatch.setenv("NOTIFY_SOCKET", "")
        assert sd_notify("READY=1") is False


class TestUnixSocketDelivery:
    def test_payload_arrives_at_unix_socket(self, tmp_path, monkeypatch):
        sock_path = tmp_path / "notify.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        srv.bind(str(sock_path))
        srv.settimeout(2.0)
        received: list[bytes] = []

        def reader():
            try:
                data, _ = srv.recvfrom(4096)
                received.append(data)
            except socket.timeout:
                pass

        t = threading.Thread(target=reader)
        t.start()
        try:
            monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
            assert sd_notify("WATCHDOG=1") is True
        finally:
            t.join(timeout=3.0)
            srv.close()

        assert received == [b"WATCHDOG=1"]

    def test_helpers_send_correct_payloads(self, tmp_path, monkeypatch):
        sock_path = tmp_path / "notify.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        srv.bind(str(sock_path))
        srv.settimeout(2.0)
        received: list[bytes] = []

        def reader():
            for _ in range(3):
                try:
                    data, _ = srv.recvfrom(4096)
                    received.append(data)
                except socket.timeout:
                    return

        t = threading.Thread(target=reader)
        t.start()
        try:
            monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
            assert notify_ready() is True
            assert notify_watchdog() is True
            assert notify_stopping() is True
        finally:
            t.join(timeout=3.0)
            srv.close()

        assert received == [b"READY=1", b"WATCHDOG=1", b"STOPPING=1"]


class TestFailureNonFatal:
    def test_unreachable_socket_returns_false(self, tmp_path, monkeypatch):
        # Path that doesn't exist — connect() should fail and we should
        # NOT raise.
        monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "nope.sock"))
        assert sd_notify("WATCHDOG=1") is False
