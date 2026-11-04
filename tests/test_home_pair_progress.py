"""`windy home pair` shows a number that moves through every long wait (Grant 11-03, via Boss: the pre-flag gate).

The waits: the phone's approval and the finishing after it (Connect's 428 stage + expires_in + waiting_for, Connect
#106), a 429's Retry-After, the memory download (bytes, of Content-Length when sent) and its import (events). The gate:
on a mock clock, no long stage goes more than 10 s without a moving number, in --json-status and for a person."""

from __future__ import annotations

import io
import json
from typing import Any

import httpx
import pytest
from rich.console import Console

from tests.test_bring_home import AGENT_MX, _memory_doc
from tests.test_home_pair import _isolated, _restore_environ  # noqa: F401 — the same isolated home per test
from tests.test_home_pair import PASSPORT, Fake, _no_secret_anywhere
from windyfly import home_pair

MAX_GAP_S = 10.0
MOVING = ("seconds_left", "seconds_in_stage", "bytes", "done")


def _many_messages(n: int) -> bytes:
    lines = [json.dumps({"type": "windy.memory.header", "format": "ndjson-v1", "event_count": n,
                         "passport_number": PASSPORT, "matrix_user_id": AGENT_MX, "rooms": ["!dm:chat"]})]
    lines += [json.dumps({"type": "m.room.message", "sender": "@nora:chat.windychat.ai", "room_id": "!dm:chat",
                          "event_id": f"${i}", "origin_server_ts": i,
                          "content": {"msgtype": "m.text", "body": f"message {i} {'x' * (i % 97)}"}})
              for i in range(n)]
    return ("\n".join(lines) + "\n").encode()


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class Timed(io.StringIO):
    """--json-status output, each line stamped with the mock clock's time it was written."""

    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self.clock, self.lines = clock, []

    def write(self, s: str) -> int:
        for line in s.splitlines():
            self.lines.append((self.clock.now(), json.loads(line)))
        return super().write(s)


class SlowFake(Fake):
    """A slow but healthy round: a long poll interval, a 429 with a long Retry-After, a long finishing stage, a memory
    that arrives in small chunks over minutes, and an import that takes a while."""

    def __init__(self, clock: Clock, *, polls, interval=15, doc=None, content_length=True, chunk=2048,
                 chunk_s=2.0) -> None:
        super().__init__(polls=polls, memory=(200, doc if doc is not None else _memory_doc()))
        self.clock, self.interval, self.content_length = clock, interval, content_length
        self.chunk, self.chunk_s = chunk, chunk_s

    def handle(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/home/code":
            self.calls.append((req.url.path, req.method, json.loads(req.content), dict(req.headers)))
            return httpx.Response(200, json={"device_code": "DEVICE-CODE-SECRET", "user_code": "1234-5678",
                                             "qr_url": "https://connect.test/home?c=12345678",
                                             "expires_in": 300, "interval": self.interval})
        if req.url.path == "/v1/home/poll" and self.polls and self.polls[0][0] == 429:
            self.polls.pop(0)
            self.calls.append((req.url.path, req.method, {}, {}))
            return httpx.Response(429, headers={"Retry-After": "60"}, json={"error": "slow_down"})
        if req.url.path == "/v1/device/memory":
            self.calls.append((req.url.path, req.method, {}, dict(req.headers)))
            body = json.dumps(self.memory[1]).encode()

            def chunks():
                for i in range(0, len(body), self.chunk):
                    self.clock.sleep(self.chunk_s)
                    yield body[i:i + self.chunk]

            headers = {"content-type": "application/json"}
            if self.content_length:
                headers["content-length"] = str(len(body))
            return httpx.Response(200, headers=headers, content=chunks())
        return super().handle(req)


def _waiting(stage: str, expires_in: int, waiting_for: list[str] | None = None) -> tuple[int, dict]:
    body: dict[str, Any] = {"error": "authorization_pending", "stage": stage, "expires_in": expires_in}
    if waiting_for is not None:
        body["waiting_for"] = waiting_for
    return 428, body


SLOW_ROUND = ([_waiting("waiting_for_approval", 300 - 15 * i) for i in range(1, 5)] + [(429, None)]
              + [_waiting("approved_finishing", 900 - 15 * i, ["chat", "mail"]) for i in range(10)]
              + [_waiting("approved_finishing", 750 - 15 * i, ["mail"]) for i in range(4)] + [(200, None)])


@pytest.fixture
def slow_import(monkeypatch):
    """Each saved message takes 50 ms of the mock clock: 3,000 messages = 150 s of import."""
    from windyfly.memory import episodes

    real = episodes.save_episode
    holder: dict[str, Clock] = {}

    def save(*a: Any, **kw: Any) -> Any:
        holder["clock"].sleep(0.05)
        return real(*a, **kw)

    monkeypatch.setattr(episodes, "save_episode", save)
    return holder


def _run(fake: SlowFake, clock: Clock, *, json_status: bool, console: Any = None) -> tuple[int, Any, str]:
    out = Timed(clock)
    buf = io.StringIO()
    console = console or Console(file=buf, width=200, color_system=None)
    rc = home_pair.pair(console, json_status=json_status, transport=fake.transport(), sleep=clock.sleep,
                        now=clock.now, out=out)
    return rc, out, buf.getvalue()


def _assert_never_frozen(lines: list[tuple[float, dict]]) -> None:
    """THE GATE: from the code to "done", every gap between two lines is at most 10 s, and two progress lines in a
    row of the same stage differ in a moving number."""
    assert lines[0][1]["event"] == "code" and lines[-1][1]["event"] == "done"
    for (t1, a), (t2, b) in zip(lines, lines[1:]):
        assert t2 - t1 <= MAX_GAP_S, f"{t2 - t1:.1f} s with nothing new between {a} and {b}"
        if a["event"] == b["event"] == "progress" and a["stage"] == b["stage"]:
            assert any(a.get(k) != b.get(k) for k in MOVING), f"a frozen number: {a} then {b}"


def test_no_long_stage_goes_10_s_without_a_moving_number(tmp_path, slow_import):
    clock = Clock()
    slow_import["clock"] = clock
    doc = _memory_doc(_many_messages(3000))
    fake = SlowFake(clock, polls=list(SLOW_ROUND), doc=doc)
    rc, out, _ = _run(fake, clock, json_status=True)
    assert rc == 0
    lines = out.lines
    _assert_never_frozen(lines)
    # every long stage was really there, and long: approval, finishing, download, import
    took: dict[str, float] = {}
    for t, e in lines:
        if e["event"] == "progress":
            took.setdefault(e["stage"], t)
    assert set(took) >= {"waiting_for_approval", "approved_finishing", "download", "import"}
    total = clock.t - lines[0][0]
    assert total > 400  # minutes of waiting, so the 10 s gate was tested over a long round
    _no_secret_anywhere(tmp_path, out.getvalue())


def test_the_gate_catches_a_frozen_wait():
    """The gate itself: a wait with no line for 11 s, or the same numbers twice, fails it."""
    code, done = {"event": "code"}, {"event": "done"}
    p = {"event": "progress", "stage": "download", "bytes": 5}
    with pytest.raises(AssertionError):
        _assert_never_frozen([(0.0, code), (11.0, done)])
    with pytest.raises(AssertionError):
        _assert_never_frozen([(0.0, code), (1.0, p), (2.0, dict(p)), (3.0, done)])
    _assert_never_frozen([(0.0, code), (1.0, p), (2.0, {**p, "bytes": 6}), (3.0, done)])


def test_the_428_stage_time_left_and_who_it_waits_for_are_shown(slow_import):
    clock = Clock()
    slow_import["clock"] = clock
    fake = SlowFake(clock, polls=list(SLOW_ROUND), interval=5)
    rc, out, _ = _run(fake, clock, json_status=True)
    assert rc == 0
    prog = [e for _, e in out.lines if e["event"] == "progress"]
    first = next(e for e in prog if e["stage"] == "waiting_for_approval")
    assert first["seconds_left"] is not None and first["seconds_left"] <= 300 and first["slow"] is False
    fin = [e for e in prog if e["stage"] == "approved_finishing"]
    assert fin[0]["waiting_for"] == ["chat", "mail"] and fin[-1]["waiting_for"] == ["mail"]
    assert fin[0]["slow"] is False and fin[-1]["slow"] is True        # past 60 s in the finishing stage
    lefts = [e["seconds_left"] for e in fin]
    assert lefts[0] > lefts[-1]                                        # counted down


def test_a_person_reads_the_stage_in_plain_words(slow_import):
    clock = Clock()
    slow_import["clock"] = clock
    fake = SlowFake(clock, polls=list(SLOW_ROUND), interval=5)
    rc, _, said = _run(fake, clock, json_status=False)
    assert rc == 0, said
    assert "Waiting for you to approve on your phone (" in said
    assert "Approved. Finishing the move (" in said and "Waiting for Windy Chat and Windy Mail." in said
    assert "Still finishing, this can take a few minutes." in said
    assert "Bringing over Pip's memory:" in said and "Saving Pip's memory on this computer:" in said
    # not a terminal (a log): one line per stage change, never a line per tick
    assert said.count("Waiting for you to approve on your phone") == 1
    assert said.count("Still finishing, this can take a few minutes.") == 1
    assert "Pip now lives on this computer" in said


def test_an_older_connect_without_a_stage_still_shows_seconds(slow_import):
    clock = Clock()
    slow_import["clock"] = clock
    fake = SlowFake(clock, polls=[(428, {"error": "authorization_pending"})] * 14 + [(200, None)], interval=5)
    rc, out, _ = _run(fake, clock, json_status=True)
    assert rc == 0
    waits = [e for _, e in out.lines if e["event"] == "progress" and e["stage"] == "waiting"]
    assert waits and waits[0]["seconds_left"] is None
    assert waits[-1]["seconds_in_stage"] > waits[0]["seconds_in_stage"] and waits[-1]["slow"] is True
    _assert_never_frozen(out.lines)


def test_an_older_connect_without_a_stage_says_waiting_in_plain_words(slow_import):
    clock = Clock()
    slow_import["clock"] = clock
    rc, _, said = _run(SlowFake(clock, polls=[(428, {})] * 14 + [(200, None)], interval=5), clock,
                       json_status=False)
    assert rc == 0, said
    assert "Waiting for Windy (" in said and "Still waiting, this can take a few minutes." in said


def test_a_download_without_content_length_counts_bytes_so_far(slow_import):
    clock = Clock()
    slow_import["clock"] = clock
    fake = SlowFake(clock, polls=[(200, None)], interval=5, content_length=False, chunk=64)
    rc, out, _ = _run(fake, clock, json_status=True)
    assert rc == 0
    downs = [e for _, e in out.lines if e["event"] == "progress" and e["stage"] == "download"]
    assert downs and all(e["total"] is None and e["percent"] is None for e in downs)
    assert downs[-1]["bytes"] > downs[0]["bytes"]


def test_a_download_with_content_length_ends_at_100_percent(slow_import):
    clock = Clock()
    slow_import["clock"] = clock
    fake = SlowFake(clock, polls=[(200, None)], interval=5, chunk=64)
    rc, out, _ = _run(fake, clock, json_status=True)
    assert rc == 0
    downs = [e for _, e in out.lines if e["event"] == "progress" and e["stage"] == "download"]
    assert downs[-1]["percent"] == 100 and downs[-1]["bytes"] == downs[-1]["total"]
    imports = [e for _, e in out.lines if e["event"] == "progress" and e["stage"] == "import"]
    assert imports[-1]["done"] == imports[-1]["total"] and imports[-1]["percent"] == 100


class _Status:
    def __init__(self, log: list, text: str) -> None:
        self.log = log
        log.append(("status", text))

    def start(self) -> None:
        self.log.append(("start",))

    def update(self, text: str) -> None:
        self.log.append(("update", text))

    def stop(self) -> None:
        self.log.append(("stop",))


class TerminalConsole:
    """A person at a terminal: progress is ONE line rewritten in place (rich's status), never a scroll of counters."""

    is_terminal = True

    def __init__(self) -> None:
        self.log: list = []

    def status(self, text: str) -> _Status:
        return _Status(self.log, text)

    def print(self, *a: Any, **kw: Any) -> None:
        self.log.append(("print", " ".join(str(x) for x in a)))


def test_at_a_terminal_the_number_moves_in_one_live_line_and_stops_before_any_other_line(slow_import):
    clock = Clock()
    slow_import["clock"] = clock
    con = TerminalConsole()
    fake = SlowFake(clock, polls=list(SLOW_ROUND), interval=5)
    rc, _, _ = _run(fake, clock, json_status=False, console=con)
    assert rc == 0
    updates = [e for e in con.log if e[0] == "update"]
    assert len(updates) > 20                                           # the number moved, many times
    live = False
    for e in con.log:
        if e[0] == "start":
            assert not live
            live = True
        elif e[0] == "stop":
            live = False
        elif e[0] == "print":
            assert not live, f"a line printed under a live status: {e}"
    assert not live                                                    # stopped at the end, every way out
    assert con.log[-1][0] == "print" and "now lives on this computer" in con.log[-1][1]


def test_a_cancel_mid_wait_stops_the_live_line(slow_import):
    clock = Clock()
    con = TerminalConsole()

    class Cancel(SlowFake):
        def handle(self, req):
            if req.url.path == "/v1/home/poll" and len([c for c in self.calls if c[0] == "/v1/home/poll"]) >= 3:
                raise KeyboardInterrupt
            return super().handle(req)

    fake = Cancel(clock, polls=[_waiting("waiting_for_approval", 290)] * 10, interval=15)
    rc, _, _ = _run(fake, clock, json_status=False, console=con)
    assert rc == 130
    kinds = [e[0] for e in con.log]
    assert "update" in kinds and kinds[-1] == "print" and "stop" in kinds
    assert kinds.index("stop") < len(kinds) - 1
