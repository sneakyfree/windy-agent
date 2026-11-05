"""`windy home install --ticket -` (Windy Word's desktop door; windy-contracts #273, Hub's draft + Agent's answers).

The kid line comes first; the ticket "<handover_id>.<pickup_code>" comes on stdin only; the pickup and the memory are
redeemed with the code + a proof by THIS computer's key; then the same settle steps as every door. The code, the
bootstrap token and the proofs never appear in any output or file."""

from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from rich.console import Console

from tests.test_bring_home import _memory_doc
from tests.test_home_pair import _isolated, _restore_environ  # noqa: F401 — the same isolated home per test
from tests.test_home_pair import BOOT, PASSPORT, Fake, _no_secret_anywhere
from windyfly import home_install, hub_hatch
from windyfly.eternitas import agent_keys as ak

HID = "ho_desk_1"
CODE = "PICKUP-CODE-SECRET-0123456789abcdef"


class Hub(Fake):
    """hub.test (the desktop pickup + memory) and eternitas.test (from Fake)."""

    def __init__(self, *, pickups=None, memory=None, **kw):
        super().__init__(**kw)
        self.pickups = list(pickups if pickups is not None else [(428, {"stage": "x", "waiting_for": ["chat", "mail"]}),
                                                                 (428, {"waiting_for": ["mail"]}), (200, None)])
        self.mem = memory if memory is not None else (200, _memory_doc())

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.startswith(f"/api/v1/agent/handover/{HID}/"):
            self.calls.append((path, req.method, {}, dict(req.headers)))
            if path.endswith("/credentials"):
                status, body = self.pickups.pop(0)
                if status == 200 and body is None:
                    body = {"eternitas": {"passport_number": PASSPORT, "bootstrap_token": BOOT,
                                          "expires_at": "2026-11-11T12:00:00Z"}, "agent_name": "Pip"}
                if isinstance(body, Exception):
                    raise body
                return httpx.Response(status, json=body)
            if path.endswith("/memory"):
                status, doc = self.mem
                return httpx.Response(status, json=doc)
        return super().handle(req)


class Lines:
    """A scripted stdin: each get(timeout) returns the next item; None = nothing yet (the clock moves on)."""

    def __init__(self, items, clock):
        self.items, self.clock = list(items), clock

    def get(self, timeout):
        item = self.items.pop(0) if self.items else None
        if item is None:
            self.clock["now"] += timeout
        return item


def _install(hub: Hub, lines_items, *, ticket="-", json_status=True):
    out, buf = io.StringIO(), io.StringIO()
    t = {"now": 1000.0}

    def sleep(s):
        t["now"] += s

    rc = home_install.install(Console(file=buf, width=200, color_system=None), ticket=ticket, json_status=json_status,
                              lines=Lines(lines_items, t), transport=hub.transport(), sleep=sleep,
                              now=lambda: t["now"], out=out)
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    return rc, events, out.getvalue() + buf.getvalue()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_the_desktop_door_brings_the_agent_home(tmp_path):
    hub = Hub()
    rc, events, text = _install(hub, [f"{HID}.{CODE}\n"])
    assert rc == 0, events
    assert events[0] == {"event": "kid", "kid": ak.local_kid()}             # the FIRST line, before anything else
    names = [e["event"] for e in events if e["event"] != "progress"]
    assert names == ["kid", "approved", "registered", "memory", "done"]
    assert hub_hatch.cloud_agent()["where"] == "home" and ak.has_registered_key()
    assert hub_hatch.cloud_agent()["door"] == "desktop"
    # the pending platforms are shown (only those pending at the moment)
    waits = [e.get("waiting_for") for e in events if e["event"] == "progress" and e["stage"] == "approved_finishing"]
    assert ["chat", "mail"] in waits and ["mail"] in waits
    # every redeem carried the code + a proof by THIS computer's key for THAT request
    redeems = [c for c in hub.calls if c[0].startswith(f"/api/v1/agent/handover/{HID}/")]
    assert [c[0].rsplit("/", 1)[1] for c in redeems] == ["credentials"] * 3 + ["memory"]
    jtis = set()
    for path, _m, _b, headers in redeems:
        assert headers["x-windy-pickup-code"] == CODE
        h, p, _s = headers["x-windy-pickup-proof"].split(".")
        header, payload = json.loads(_b64d(h)), json.loads(_b64d(p))
        assert header["typ"] == "windy-pickup-proof+jwt" and header["alg"] == "ES256"
        assert header["kid"] == ak.local_kid() == ak.thumbprint(header["jwk"])
        assert ak.verify_jws(headers["x-windy-pickup-proof"], header["jwk"]) is not None
        assert payload["handover_id"] == HID and payload["htm"] == "GET"
        assert payload["htu"] == f"https://hub.test{path}"
        jtis.add(payload["jti"])
    assert len(jtis) == len(redeems)                                          # a fresh jti every time
    _no_secret_anywhere(tmp_path, text)
    assert CODE not in text
    for f in tmp_path.rglob("*"):                                             # never written to disk (P8)
        if f.is_file():
            assert CODE.encode() not in f.read_bytes(), f


@pytest.fixture(autouse=True)
def _hub_url(monkeypatch):
    monkeypatch.setenv("WINDY_HUB_URL", "https://hub.test")


def test_the_ticket_comes_on_stdin_only_and_is_never_echoed(tmp_path):
    hub = Hub()
    rc, events, text = _install(hub, [], ticket=f"{HID}.{CODE}")
    assert rc == 2 and events[0]["code"] == "ticket_from_stdin_only" and CODE not in text
    assert hub.calls == [] and not ak.load_credentials()                    # no key, no call


def test_stdin_closing_ends_the_round_and_undoes_the_key(tmp_path):
    hub = Hub()
    rc, events, _ = _install(hub, [home_install.EOF_LINE])
    assert rc == 1 and events[0]["event"] == "kid" and events[-1]["code"] == "stdin_closed"
    assert hub.calls == [] and not ak.load_credentials()


def test_the_stdin_wait_shows_a_moving_number_and_gives_up_after_15_minutes(tmp_path):
    hub = Hub()
    rc, events, _ = _install(hub, [None] * 400)
    assert rc == 1 and events[-1]["code"] == "stdin_timeout"
    ticks = [e["seconds_in_stage"] for e in events if e["event"] == "progress"]
    assert ticks == sorted(ticks) and len(set(ticks)) == len(ticks) and ticks[-1] < home_install.STDIN_WAIT_S
    assert all(b - a <= 5 for a, b in zip(ticks, ticks[1:]))
    assert not ak.load_credentials()


@pytest.mark.parametrize("ticket", ["no-dot-here", f".{CODE}", f"{HID}.", f"{HID}.short", f"bad id!.{CODE}"])
def test_a_bad_ticket_is_refused_without_a_call(tmp_path, ticket):
    hub = Hub()
    rc, events, text = _install(hub, [ticket + "\n"])
    assert rc == 1 and events[-1]["code"] == "bad_ticket" and hub.calls == []
    assert not ak.load_credentials()


@pytest.mark.parametrize("status,body,code", [
    (401, {"error": "pickup_code_invalid"}, "pickup_code_invalid"),
    (403, {"error": "handover_off"}, "handover_off"),
    (403, {"error": "proof_kid_mismatch"}, "proof_kid_mismatch"),
    (404, {}, "handover_not_found"),
    (410, {"error": "gone"}, "gone"),
])
def test_a_final_refusal_changes_nothing(tmp_path, status, body, code):
    hub = Hub(pickups=[(status, body)])
    rc, events, _ = _install(hub, [f"{HID}.{CODE}\n"])
    assert rc == 1 and events[-1]["code"] == code
    assert not ak.load_credentials() and hub_hatch.cloud_agent().get("where") != "home"


def test_a_busy_hub_is_retried_not_final(tmp_path):
    hub = Hub(pickups=[(503, {}), (502, {}), (200, None)])
    rc, events, _ = _install(hub, [f"{HID}.{CODE}\n"])
    assert rc == 0 and hub_hatch.cloud_agent()["where"] == "home"


def test_a_refused_key_after_the_pickup_says_the_agent_is_running_nowhere(tmp_path):
    hub = Hub(keys_status=401)
    rc, events, text = _install(hub, [f"{HID}.{CODE}\n"], json_status=False)
    flat = " ".join(text.split())
    assert rc == 1 and "isn't running anywhere right now" in flat


def test_no_memory_in_the_pickup_means_no_memory_call(tmp_path):
    class NoMemory(Hub):
        def handle(self, req):
            if req.url.path.endswith("/credentials") and self.pickups and self.pickups[0][0] == 200:
                self.pickups.pop(0)
                self.calls.append((req.url.path, req.method, {}, dict(req.headers)))
                return httpx.Response(200, json={"eternitas": {"passport_number": PASSPORT, "bootstrap_token": BOOT},
                                                 "agent_name": "Pip", "memory": None})
            return super().handle(req)

    hub = NoMemory(pickups=[(200, None)])
    rc, _, _ = _install(hub, [f"{HID}.{CODE}\n"])
    assert rc == 0 and not any(c[0].endswith("/memory") for c in hub.calls)


# ── Hub 11-05: the desktop door's way back, and finishing a round that stopped after its registration ──────────────

def test_a_refused_key_tells_a_person_to_start_again_in_windy_word(tmp_path):
    hub = Hub(keys_status=401)
    rc, events, text = _install(hub, [f"{HID}.{CODE}\n"], json_status=False)
    flat = " ".join(text.split())
    assert rc == 1 and "Start the move again in Windy Word." in flat and "Run this again" not in flat


def _stop_after_registration(monkeypatch):
    from windyfly import bring_home

    real = bring_home.ensure_brain
    monkeypatch.setattr(bring_home, "ensure_brain", lambda name: (_ for _ in ()).throw(OSError("disk full")))
    return lambda: monkeypatch.setattr(bring_home, "ensure_brain", real)


def test_a_round_that_stopped_after_registration_is_finished_with_no_new_key(tmp_path, monkeypatch):
    restore = _stop_after_registration(monkeypatch)
    rc, events, _ = _install(Hub(), [f"{HID}.{CODE}\n"])
    assert rc == 1 and events[-1]["code"] == "internal" and ak.has_registered_key()
    kid = ak.local_kid()
    restore()
    hub = Hub()
    rc, events, text = _install(hub, [f"{HID}.{CODE}\n"])                  # Word sends a ticket anyway: never used
    assert rc == 0, events
    names = [e["event"] for e in events if e["event"] != "progress"]
    assert names == ["resumed", "memory", "done"]                          # no kid line, no new pickup
    assert events[0] == {"event": "resumed", "passport": PASSPORT}
    assert ak.local_kid() == kid and hub.calls == []                       # same key; no call that needs the code
    assert events[1]["status"] == "lost"                                   # the spent memory link: reported, not fetched
    assert hub_hatch.cloud_agent()["where"] == "home"
    assert CODE not in text


def test_another_passports_marker_is_never_finished(tmp_path, monkeypatch):
    from windyfly import home_pair

    restore = _stop_after_registration(monkeypatch)
    _install(Hub(), [f"{HID}.{CODE}\n"])
    restore()
    marker = json.loads(home_pair._pending_path().read_text())
    marker["passport_number"] = "ET26-OTHR-0001"
    home_pair._pending_path().write_text(json.dumps(marker))
    hub = Hub()
    rc, events, _ = _install(hub, [f"{HID}.{CODE}\n"])
    assert rc == 1 and events == [{"event": "error", "code": "another_agent_here"}] and hub.calls == []


def test_the_already_home_line_escapes_the_name(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_hatch, "cloud_agent", lambda: {"where": "home", "agent_name": "[bold]Pip[/bold]"})
    rc, _, text = _install(Hub(), [], json_status=False)
    assert rc == 1 and "[bold]Pip[/bold] already lives on this computer." in text
