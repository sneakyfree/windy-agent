"""Channel manager start_all() failure semantics.

Regression coverage for the 2026-05-17 outage: a missing optional
dep (``python-telegram-bot``) made the Telegram channel ImportError
at ``start()``. The pre-fix manager swallowed the exception with
``logger.error`` and returned normally — so ``windyfly.main`` called
``notify_ready()`` against systemd and the watchdog SIGABRT'd a
useless process every 10 min for 3 days.

Contract these tests pin down:

1. A registered channel whose ``start()`` raises causes
   ``start_all()`` to raise ``ChannelStartupError``.
2. The error carries the list of (name, exception) pairs so callers
   can log a precise reason.
3. Channels that started OK earlier in the loop are NOT rolled back —
   they're left running so the caller can stop_all() as part of
   normal shutdown.
4. The all-success path still returns ``None`` (no exception) — we
   didn't regress the happy path.
"""

from __future__ import annotations

import pytest

from windyfly.channels.base import ChannelAdapter, OutgoingMessage
from windyfly.channels.manager import ChannelManager, ChannelStartupError


class _FakeChannel(ChannelAdapter):
    """Minimal ChannelAdapter that records lifecycle calls."""

    def __init__(self, name: str, *, fail_on_start: bool = False) -> None:
        self.name = name
        self._fail_on_start = fail_on_start
        self.start_called = False
        self.stop_called = False
        self._connected = False

    async def start(self) -> None:
        self.start_called = True
        if self._fail_on_start:
            raise ImportError(f"simulated missing dep for {self.name}")
        self._connected = True

    async def stop(self) -> None:
        self.stop_called = True
        self._connected = False

    async def send(self, message: OutgoingMessage) -> None:
        pass

    def is_connected(self) -> bool:
        return self._connected


def _noop_respond(text: str, session_id: str) -> str:
    return "ok"


@pytest.mark.asyncio
async def test_start_all_happy_path_returns_none():
    mgr = ChannelManager(_noop_respond)
    a = _FakeChannel("a")
    b = _FakeChannel("b")
    mgr.register(a)
    mgr.register(b)

    result = await mgr.start_all()

    assert result is None
    assert a.start_called and a.is_connected()
    assert b.start_called and b.is_connected()


@pytest.mark.asyncio
async def test_start_all_raises_on_single_failure():
    mgr = ChannelManager(_noop_respond)
    bad = _FakeChannel("telegram", fail_on_start=True)
    mgr.register(bad)

    with pytest.raises(ChannelStartupError) as excinfo:
        await mgr.start_all()

    assert "telegram" in str(excinfo.value)
    assert len(excinfo.value.failures) == 1
    name, exc = excinfo.value.failures[0]
    assert name == "telegram"
    assert isinstance(exc, ImportError)


@pytest.mark.asyncio
async def test_start_all_collects_multiple_failures():
    mgr = ChannelManager(_noop_respond)
    mgr.register(_FakeChannel("telegram", fail_on_start=True))
    mgr.register(_FakeChannel("discord", fail_on_start=True))

    with pytest.raises(ChannelStartupError) as excinfo:
        await mgr.start_all()

    failure_names = {n for n, _ in excinfo.value.failures}
    assert failure_names == {"telegram", "discord"}


@pytest.mark.asyncio
async def test_start_all_leaves_earlier_successes_running():
    """A late failure should NOT roll back channels that already
    started — the caller still needs to stop_all() them cleanly."""
    mgr = ChannelManager(_noop_respond)
    good = _FakeChannel("good")
    bad = _FakeChannel("bad", fail_on_start=True)
    mgr.register(good)
    mgr.register(bad)

    with pytest.raises(ChannelStartupError):
        await mgr.start_all()

    assert good.is_connected(), (
        "Earlier successful starts must stay live so stop_all() "
        "can shut them down cleanly during exit."
    )
    assert not bad.is_connected()
