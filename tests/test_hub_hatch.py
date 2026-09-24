"""`windy go` opens the ONE hub ceremony via a hatch ticket (ADR-059; the default).

Contract: ~/windy-orchestra/specs/HATCH_CEREMONY_PAGE.md §1 + §4.
All HTTP is a fake hub (httpx.MockTransport); nothing here calls prod.
"""

from __future__ import annotations

import io
import json
import logging
import os
import stat
from datetime import datetime, timezone

import httpx
import pytest
from rich.console import Console

from windyfly import hub_hatch, hub_login

TOKEN = "hub-access-token-SECRET"
T0 = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WINDY_HUB_URL", "https://hub.test")
    for var in ("WINDY_HATCH_VIA_HUB", "WINDY_HATCH_ADOPT_EXISTING", "WINDY_HATCH_NONINTERACTIVE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(hub_login, "get_access_token", lambda **_: TOKEN)


class Clock:
    def __init__(self):
        self.t = T0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def ticket_body(**kw):
    body = {"ticket_id": "tk_1", "ceremony_url": "https://app.windyword.ai/hatch?ticket=tk_1",
            "user_code": "BCDF-2345", "expires_at": _iso(T0 + 900),
            "poll_url": "/api/v1/agent/hatch/tickets/tk_1", "poll_interval_s": 3}
    body.update(kw)
    return body


BORN = {"passport_number": "ET26-HUB1-0001", "agent": {"name": "Pip", "bot_identity_id": "b1"},
        "platforms": {"chat": {"status": "ok", "matrix_user_id": "@agent_et26-hub1-0001:chat.windychat.ai",
                               "dm_room_id": "!dm:chat.windychat.ai"}, "mail": {"status": "ok"}}}


class FakeHub:
    """Answers POST tickets with `create`, each poll with the next of `polls`."""

    def __init__(self, create=(201, None), polls=(), cancel=(200, {"status": "cancelled"})):
        self.create = create
        self.polls = list(polls)
        self.cancel = cancel
        self.requests: list[httpx.Request] = []

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/agent/hatch/tickets":
            status, body = self.create
            return httpx.Response(status, json=body if body is not None else ticket_body())
        if request.method == "POST" and path.endswith("/cancel"):
            return httpx.Response(self.cancel[0], json=self.cancel[1])
        if request.method == "GET" and path == "/api/v1/agent/hatch/tickets/tk_1":
            item = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
            if isinstance(item, BaseException):
                raise item
            status, body, *headers = item
            return httpx.Response(status, json=body, headers=headers[0] if headers else None)
        return httpx.Response(404, json={"error": "not_found"})


def _console():
    buf = io.StringIO()
    return Console(file=buf, width=200, color_system=None), buf


def _go(hub: FakeHub, clock: Clock | None = None, **kw):
    clock = clock or Clock()
    console, buf = _console()
    opened: list[str] = []
    rc = hub_hatch.go(console, transport=hub.transport(), sleep=clock.sleep, now=clock.now,
                      open_browser=opened.append, **kw)
    return rc, buf.getvalue(), opened, clock


# ── the ticket ───────────────────────────────────────────────────────

def test_ticket_request_matches_the_contract_and_mints_nothing_locally(tmp_path):
    hub = FakeHub(polls=[(200, {"status": "pending"}), (200, {"status": "complete", "result": BORN})])
    rc, out, opened, _ = _go(hub)
    post = hub.requests[0]
    assert post.method == "POST" and str(post.url) == "https://hub.test/api/v1/agent/hatch/tickets"
    assert post.headers["Authorization"] == f"Bearer {TOKEN}"
    assert 1 <= len(post.headers["Idempotency-Key"]) <= 128
    assert json.loads(post.content) == {"source": "cli", "then": "stay", "return_hint": "terminal"}
    assert rc == 0
    assert "Open this to hatch your agent: https://app.windyword.ai/hatch?ticket=tk_1" in out
    assert "BCDF-2345" in out and opened == ["https://app.windyword.ai/hatch?ticket=tk_1"]


def test_agent_name_is_sent_when_given():
    hub = FakeHub(polls=[(200, {"status": "complete", "result": BORN})])
    _go(hub, agent_name="Pip")
    assert json.loads(hub.requests[0].content)["agent_name"] == "Pip"


def test_browser_failure_is_quiet():
    hub = FakeHub(polls=[(200, {"status": "complete", "result": BORN})])
    console, buf = _console()

    def broken(url):
        raise RuntimeError("no display")

    clock = Clock()
    rc = hub_hatch.go(console, transport=hub.transport(), sleep=clock.sleep, now=clock.now, open_browser=broken)
    assert rc == 0 and "It's alive!" in buf.getvalue()


# ── statuses ─────────────────────────────────────────────────────────

def test_complete_stays_in_the_cloud(tmp_path):
    hub = FakeHub(polls=[(200, {"status": "in_ceremony"}), (200, {"status": "complete", "result": BORN})])
    rc, out, _, _ = _go(hub)
    assert rc == 0
    assert "It's alive! Say hi: https://app.windychat.ai/?agent_room=%21dm%3Achat.windychat.ai" in out
    rec = hub_hatch.cloud_agent()
    assert rec["passport_number"] == "ET26-HUB1-0001" and rec["where"] == "cloud"
    assert stat.S_IMODE(os.stat(hub_hatch.cloud_agent_path()).st_mode) == 0o600
    # Nothing local: no EPT, no passport in the process env or a .env.
    assert "ETERNITAS_PASSPORT_TOKEN" not in os.environ
    assert not list(tmp_path.rglob(".env"))


def test_alive_shows_the_telemetry_disclosure_once():
    from windyfly.observability import disclosure

    assert not disclosure.disclosed()
    hub = FakeHub(polls=[(200, {"status": "complete", "result": BORN})])
    rc, out, _, _ = _go(hub)
    assert rc == 0 and "It's alive!" in out
    flat = " ".join(out.split())
    assert "never your messages" in flat and "WINDY_TELEMETRY=0" in flat
    assert disclosure.disclosed()


def test_partial_is_born_and_says_the_hub_finishes():
    hub = FakeHub(polls=[(200, {"status": "partial", "result": BORN})])
    rc, out, _, _ = _go(hub)
    assert rc == 0 and "It's alive!" in out and "the hub finishes them" in out


@pytest.mark.parametrize("status, words", [
    ("failed", "The hatch failed"),
    ("expired", "The ceremony link expired"),
    ("cancelled", "The ceremony was cancelled"),
])
def test_dead_tickets_are_said_plainly_and_rotate_the_key(status, words):
    key_before = hub_hatch.idempotency_key()
    hub = FakeHub(polls=[(200, {"status": status, "stage": "eternitas", "code": "issuer_timeout"})
                         if status == "failed" else (200, {"status": status})])
    rc, out, _, _ = _go(hub)
    assert rc == 1 and words in out
    assert ("Nothing was created" in out) is (status != "failed")
    assert hub_hatch.cloud_agent() == {}
    assert hub_hatch.idempotency_key() != key_before
    if status == "failed":
        assert "stage eternitas" in out and "code issuer_timeout" in out


def test_pending_ticket_expires_at_expires_at():
    clock = Clock()
    hub = FakeHub(create=(201, ticket_body(expires_at=_iso(T0 + 10))), polls=[(200, {"status": "pending"})])
    rc, out, _, clock = _go(hub, clock)
    assert rc == 1 and "expired" in out
    assert clock.t <= T0 + 10 + 3


def test_in_ceremony_is_not_cut_off_by_expires_at():
    hub = FakeHub(create=(201, ticket_body(expires_at=_iso(T0 + 5))),
                  polls=[(200, {"status": "in_ceremony"}), (200, {"status": "in_ceremony"}),
                         (200, {"status": "in_ceremony"}), (200, {"status": "complete", "result": BORN})])
    rc, out, _, _ = _go(hub)
    assert rc == 0 and "It's alive!" in out


# ── polling discipline ───────────────────────────────────────────────

def test_poll_interval_is_never_below_two_seconds():
    hub = FakeHub(create=(201, ticket_body(poll_interval_s=0.1)),
                  polls=[(200, {"status": "pending"}), (200, {"status": "complete", "result": BORN})])
    _, _, _, clock = _go(hub)
    assert clock.sleeps and min(clock.sleeps) >= 2.0


def test_429_honours_retry_after():
    hub = FakeHub(polls=[(429, {"error": "slow_down"}, {"Retry-After": "7"}),
                         (200, {"status": "complete", "result": BORN})])
    _, _, _, clock = _go(hub)
    assert clock.sleeps == [3.0, 7.0]


def test_transient_poll_errors_keep_polling():
    hub = FakeHub(polls=[httpx.ConnectError("blip"), (503, {}), (200, {"status": "complete", "result": BORN})])
    rc, out, _, _ = _go(hub)
    assert rc == 0 and "It's alive!" in out


def test_poll_url_absolute_is_used_as_is():
    hub = FakeHub(create=(201, ticket_body(poll_url="https://hub.test/api/v1/agent/hatch/tickets/tk_1")),
                  polls=[(200, {"status": "complete", "result": BORN})])
    rc, _, _, _ = _go(hub)
    assert rc == 0
    assert str(hub.requests[1].url) == "https://hub.test/api/v1/agent/hatch/tickets/tk_1"


# ── Ctrl-C ───────────────────────────────────────────────────────────

def test_ctrl_c_cancels_the_ticket_and_exits_cleanly():
    hub = FakeHub(polls=[(200, {"status": "pending"})])
    clock = Clock()
    calls = {"n": 0}

    def sleep(s):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        clock.sleep(s)

    console, buf = _console()
    rc = hub_hatch.go(console, transport=hub.transport(), sleep=sleep, now=clock.now, open_browser=lambda u: None)
    cancels = [r for r in hub.requests if r.url.path.endswith("/cancel")]
    assert rc == 1 and len(cancels) == 1
    assert cancels[0].url.path == "/api/v1/agent/hatch/tickets/tk_1/cancel"
    assert cancels[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert "Cancelled" in buf.getvalue() and "Nothing was created" in buf.getvalue()


# ── idempotency ──────────────────────────────────────────────────────

def test_idempotency_key_is_stable_and_private():
    first = hub_hatch.idempotency_key()
    assert hub_hatch.idempotency_key() == first
    path = hub_hatch.install_key_path()
    assert path.parent == hub_login.session_path().parent
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_retry_of_a_pending_ceremony_reuses_the_key():
    hub = FakeHub(polls=[(200, {"status": "pending"})])
    hub_hatch.create_ticket(TOKEN, transport=hub.transport())
    hub_hatch.create_ticket(TOKEN, transport=hub.transport())
    assert hub.requests[0].headers["Idempotency-Key"] == hub.requests[1].headers["Idempotency-Key"]


def test_other_installs_get_other_keys(tmp_path, monkeypatch):
    mine = hub_hatch.idempotency_key()
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "other"))
    assert hub_hatch.idempotency_key() != mine


# ── owner already has an agent / refusals ────────────────────────────

OWNER_HAS = (409, {"error": "owner_has_agent", "passport_number": "ET26-OLD0-0001", "agent": {"name": "Windy Zero"}})


def test_owner_has_agent_never_adopts_silently(monkeypatch):
    monkeypatch.setenv("WINDY_HATCH_NONINTERACTIVE", "1")
    rc, out, opened, _ = _go(FakeHub(create=OWNER_HAS))
    assert rc == 1 and "already has Windy Zero (ET26-OLD0-0001)" in out
    assert hub_hatch.cloud_agent() == {} and opened == []


def test_owner_has_agent_accepted_is_remembered_as_cloud(monkeypatch):
    monkeypatch.setenv("WINDY_HATCH_ADOPT_EXISTING", "1")
    rc, out, _, _ = _go(FakeHub(create=OWNER_HAS))
    assert rc == 0 and hub_hatch.cloud_agent()["passport_number"] == "ET26-OLD0-0001"
    assert "windy bring-home" in out


def test_email_unverified():
    rc, out, _, _ = _go(FakeHub(create=(403, {"error": "email_unverified"})))
    assert rc == 1 and "Verify your Windy account's email" in out


def test_hub_error_is_reported():
    rc, out, _, _ = _go(FakeHub(create=(500, {"error": "boom"})))
    assert rc == 1 and "HTTP 500" in out and "boom" in out


def test_no_sign_in_no_call(monkeypatch):
    monkeypatch.setattr(hub_login, "get_access_token", lambda **_: None)

    def fail_login(**_):
        raise hub_login.LoginError("closed the browser")

    monkeypatch.setattr(hub_login, "login", fail_login)
    hub = FakeHub()
    rc, out, _, _ = _go(hub)
    assert rc == 1 and hub.requests == [] and "Sign-in didn't finish" in out


# ── the second `windy go` and bring-home ─────────────────────────────

def test_second_go_says_you_already_have_it_in_the_cloud():
    _go(FakeHub(polls=[(200, {"status": "complete", "result": BORN})]))
    hub = FakeHub()
    rc, out, _, _ = _go(hub)
    assert rc == 0 and hub.requests == []
    assert "You already have Pip (ET26-HUB1-0001) in the cloud" in out and "windy bring-home" in out


def test_bring_home_without_a_cloud_agent_says_run_windy_go(monkeypatch):
    import windyfly.cli as cli

    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("bring-home must not call out"))
    console, buf = _console()
    monkeypatch.setattr(cli, "console", console)
    with pytest.raises(SystemExit) as exit_info:
        cli._cmd_bring_home(None)
    assert exit_info.value.code == 1
    assert "No cloud agent on this machine" in buf.getvalue() and "windy go" in buf.getvalue()


# ── secrets and the flag ─────────────────────────────────────────────

def test_no_token_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    _go(FakeHub(polls=[(200, {"status": "complete", "result": BORN})]))
    assert TOKEN not in caplog.text


def test_default_goes_to_the_ceremony(monkeypatch):
    from windyfly import quickstart

    monkeypatch.delenv("WINDY_HATCH_VIA_HUB", raising=False)
    seen = {}
    monkeypatch.setattr(hub_hatch, "go", lambda console, **kw: seen.update(kw) or 0)
    monkeypatch.setattr(quickstart, "_go_keyless", lambda args: pytest.fail("old path used by default"))

    class Args:
        force = True
        key = None
        keyless = True
        no_browser = False

    quickstart.cmd_go(Args())
    assert seen["force"] is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_opt_out_keeps_the_old_path_and_says_it_is_going(monkeypatch, value):
    from windyfly import quickstart

    monkeypatch.setenv("WINDY_HATCH_VIA_HUB", value)
    monkeypatch.setattr(hub_hatch, "go", lambda *a, **k: pytest.fail("ceremony used despite the opt-out"))
    ran = []
    monkeypatch.setattr(quickstart, "_go_keyless", lambda args: ran.append("keyless"))
    console, buf = _console()
    monkeypatch.setattr(quickstart, "console", console)

    class Args:
        force = False
        key = None
        keyless = True

    quickstart.cmd_go(Args())
    assert ran == ["keyless"]
    assert "the old terminal hatch goes away in the next release" in buf.getvalue()


@pytest.mark.parametrize("value, on", [(None, True), ("1", True), ("", True), ("0", False), ("off", False)])
def test_flag_values(monkeypatch, value, on):
    if value is None:
        monkeypatch.delenv("WINDY_HATCH_VIA_HUB", raising=False)
    else:
        monkeypatch.setenv("WINDY_HATCH_VIA_HUB", value)
    assert hub_hatch.enabled() is on


def test_expiry_parse_tolerates_garbage():
    assert hub_hatch._deadline("not-a-date", T0) == T0 + 900
    assert hub_hatch._deadline(_iso(T0 + 60), T0) == pytest.approx(T0 + 60)


# ── the chat link ────────────────────────────────────────────────────

def test_hub_chat_url_wins_when_present():
    born = json.loads(json.dumps(BORN))
    born["platforms"]["chat"]["url"] = "https://app.windychat.ai/dm/pip"
    rc, out, _, _ = _go(FakeHub(polls=[(200, {"status": "complete", "result": born})]))
    assert "It's alive! Say hi: https://app.windychat.ai/dm/pip" in out
    assert hub_hatch.cloud_agent()["chat_url"] == "https://app.windychat.ai/dm/pip"


def test_no_room_falls_back_to_the_handle():
    born = json.loads(json.dumps(BORN))
    del born["platforms"]["chat"]["dm_room_id"]
    rc, out, _, _ = _go(FakeHub(polls=[(200, {"status": "complete", "result": born})]))
    assert "It's alive! Say hi at @agent_et26-hub1-0001:chat.windychat.ai in Windy Chat" in out


def test_second_go_repeats_the_link():
    _go(FakeHub(polls=[(200, {"status": "complete", "result": BORN})]))
    _, out, _, _ = _go(FakeHub())
    assert "Say hi: https://app.windychat.ai/?agent_room=" in out
