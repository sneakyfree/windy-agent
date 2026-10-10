"""Housing-history report (Eternitas housing.v1): dark by default, one POST, silent on every failure."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from windyfly.eternitas import housing

PASSPORT = "ET26-TEST-HOUS"
SERIAL = "SERIAL-DO-NOT-LOG-123"

# housing.v1.json Snapshot / ReportRequest, as a local fence against drift.
SNAPSHOT_KEYS = {"os", "arch", "runtime", "cpu_model", "gpu_model", "storage_type", "storage_gb",
                 "connection_type", "virtual", "serial"}
HOST_CLASSES = {"laptop", "desktop", "server", "phone", "cloud", "unknown"}
STORAGE_TYPES = {"nvme", "ssd", "hdd", "emmc", "network", "unknown"}
CONNECTIONS = {"ethernet", "wifi", "cellular", "other", "unknown"}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_HOUSING_REPORT", "1")
    monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("windyfly.eternitas.agent_keys.request_agent_token", lambda aud: {"token": f"t-{aud}"})
    monkeypatch.setattr("windyfly.eternitas.agent_keys.service_dpop", lambda m, u: f"dpop:{m}")
    monkeypatch.setattr(housing, "build_report", lambda: {
        "host": "h-1", "host_class": "desktop", "snapshot": {"os": "linux", "serial": SERIAL}})


def _server(*statuses, body=None):
    calls, seq = [], list(statuses)

    def handle(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        code = seq.pop(0) if len(seq) > 1 else seq[0]
        return httpx.Response(code, json=body if body is not None else {"passport": PASSPORT, "recorded": True,
                                                                         "snapshot": "recorded"})

    return httpx.MockTransport(handle), calls


def test_on_by_default_and_the_owner_switch_turns_it_off(monkeypatch):
    monkeypatch.delenv("WINDY_HOUSING_REPORT")
    assert housing.DEFAULT_ON is True and housing.enabled() is True
    monkeypatch.setenv("WINDY_HOUSING_REPORT", "0")
    transport, calls = _server(201)
    assert housing.report_once(transport=transport) == "off"
    assert housing.report_in_background() is None
    assert calls == []


def test_a_hanging_eternitas_never_delays_the_caller(monkeypatch):
    """Boss's fail-open term: the boot step returns at once on a daemon thread, whatever Eternitas does."""
    import threading
    import time

    release = threading.Event()
    monkeypatch.setattr("windyfly.eternitas.agent_keys.disabled", lambda: False)
    monkeypatch.setattr(housing, "report_once", lambda **kw: release.wait(30))
    t0 = time.monotonic()
    t = housing.report_in_background()
    assert time.monotonic() - t0 < 0.5
    assert t is not None and t.daemon and t.is_alive()  # still "waiting on Eternitas"; the agent went on
    release.set()
    t.join(5)


def test_a_server_that_never_answers_times_out_quietly(monkeypatch):
    """One attempt that gives up after _TIMEOUT: a real socket that accepts and never replies."""
    import socket
    import time

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    monkeypatch.setenv("ETERNITAS_URL", f"http://127.0.0.1:{srv.getsockname()[1]}")
    monkeypatch.setattr(housing, "_TIMEOUT", 0.5)
    monkeypatch.setattr("windyfly.agent.service_auth.agent_headers", lambda aud, m, u: {"Authorization": "x"})
    t0 = time.monotonic()
    assert housing.report_once() == "unreachable"
    assert time.monotonic() - t0 < 3
    srv.close()


def test_owner_switch_off_beats_a_default_on(monkeypatch):
    monkeypatch.setattr(housing, "DEFAULT_ON", True)
    monkeypatch.delenv("WINDY_HOUSING_REPORT")
    assert housing.enabled() is True
    monkeypatch.setenv("WINDY_HOUSING_REPORT", "0")
    assert housing.enabled() is False


def test_one_post_with_the_eternitas_audience_token():
    transport, calls = _server(201)
    assert housing.report_once(transport=transport) == "recorded"
    assert len(calls) == 1
    req = calls[0]
    assert (req.method, str(req.url)) == ("POST", f"https://api.eternitas.ai/api/v1/bots/{PASSPORT}/housing")
    assert req.headers["authorization"] == "Bearer t-windy-eternitas"
    assert json.loads(req.content) == {"host": "h-1", "host_class": "desktop",
                                       "snapshot": {"os": "linux", "serial": SERIAL}}


@pytest.mark.parametrize("word", ["recorded", "unchanged", "not_collected", "none"])
def test_every_answer_word_is_returned_not_retried(word):
    transport, calls = _server(201, body={"passport": PASSPORT, "recorded": False, "snapshot": word})
    assert housing.report_once(transport=transport) == word
    assert len(calls) == 1


def test_a_stale_token_is_reminted_once():
    transport, calls = _server(401, 201)
    assert housing.report_once(transport=transport) == "recorded"
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 403, 404, 422, 429, 500])
def test_refusals_are_a_quiet_reason_never_an_exception(status):
    transport, calls = _server(status)
    assert housing.report_once(transport=transport) == f"http_{status}"
    assert len(calls) <= 2


def test_unreachable_is_quiet():
    def boom(req):
        raise httpx.ConnectError("down")

    assert housing.report_once(transport=httpx.MockTransport(boom)) == "unreachable"


def test_no_passport_and_no_key_skip_without_a_call(monkeypatch):
    transport, calls = _server(201)
    monkeypatch.delenv("ETERNITAS_PASSPORT")
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    assert housing.report_once(transport=transport) == "no_passport"
    monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)

    def no_key(aud):
        from windyfly.eternitas.agent_keys import AgentTokenError
        raise AgentTokenError("no_key")

    monkeypatch.setattr("windyfly.eternitas.agent_keys.request_agent_token", no_key)
    assert housing.report_once(transport=transport) == "no_token"
    assert calls == []


def test_the_serial_never_reaches_a_log(caplog):
    caplog.set_level(logging.DEBUG)
    for status in (201, 500):
        transport, _ = _server(status)
        housing.report_once(transport=transport)

    def boom(req):
        raise RuntimeError(SERIAL)

    housing.report_once(transport=httpx.MockTransport(boom))
    assert SERIAL not in caplog.text


# ── the real report builder, against the contract ────────────────────

def test_real_report_fits_the_contract(monkeypatch):
    monkeypatch.undo()  # the autouse stubs: use the real build_report
    monkeypatch.setenv("WINDY_STATE_DIR", "/nonexistent-windy-state")
    body = housing.build_report()
    assert set(body) == {"host", "host_class", "snapshot"}
    assert 1 <= len(body["host"]) <= 200
    assert body["host_class"] in HOST_CLASSES
    snap = body["snapshot"]
    assert set(snap) <= SNAPSHOT_KEYS
    assert snap["os"] and snap["runtime"].startswith("windyfly ")
    assert snap.get("storage_type", "unknown") in STORAGE_TYPES
    assert snap.get("connection_type", "unknown") in CONNECTIONS
    assert all(v is not None for v in snap.values())  # unknown facts are left out, never null
    assert isinstance(snap.get("storage_gb", 0), int)
    assert len(snap.get("cpu_model", "")) <= 120 and len(snap.get("serial", "")) <= 64


def test_host_id_is_stable_and_never_the_hostname(monkeypatch):
    import socket
    monkeypatch.setattr(housing, "_machine_id", lambda: "abc-machine-id")
    a = housing.host_id()
    assert a == housing.host_id() and a != "abc-machine-id" and a != socket.gethostname()


def test_host_id_without_a_machine_id_is_a_saved_random_id(monkeypatch, tmp_path):
    monkeypatch.setattr(housing, "_machine_id", lambda: "")
    first = housing.host_id()
    assert first == housing.host_id()
    assert (tmp_path / "housing_host_id").read_text() == first


def test_firmware_junk_serials_are_not_sent(monkeypatch):
    monkeypatch.setattr(housing, "_os_name", lambda: "linux")
    for junk in ("To Be Filled By O.E.M.", "Default string", "0", "None", ""):
        monkeypatch.setattr(housing, "_read", lambda path, j=junk: j)
        assert housing._serial() is None  # noqa: SLF001
    monkeypatch.setattr(housing, "_read", lambda path: "C02REAL12345")
    assert housing._serial() == "C02REAL12345"  # noqa: SLF001


def test_boot_step_is_wired_optional_and_after_agent_keys():
    from windyfly.agent import boot
    steps = boot.default_capability_registration_sequence()
    names = [s.name for s in steps]
    assert names.index("eternitas.housing") > names.index("eternitas.agent_keys")
    assert steps[names.index("eternitas.housing")].optional is True


def test_x86_numeric_cpu_model_line_is_not_the_cpu_name(monkeypatch):
    monkeypatch.setattr(housing, "_os_name", lambda: "linux")
    monkeypatch.setattr(housing, "_read", lambda path: "model\t\t: 158\nmodel name\t: Intel(R) Core(TM) i7\n")
    assert housing._cpu_model() == "Intel(R) Core(TM) i7"  # noqa: SLF001
    monkeypatch.setattr(housing, "_read", lambda path: "model\t\t: 158\n")
    assert not housing._cpu_model()  # noqa: SLF001  (a bare number is not a name)


def test_apple_product_name_beats_an_unreliable_chassis_type(monkeypatch):
    monkeypatch.setattr(housing, "_os_name", lambda: "linux")
    monkeypatch.delenv("ANDROID_ROOT", raising=False)
    files = {"/sys/class/dmi/id/product_name": "iMac18,3", "/sys/class/dmi/id/chassis_type": "9"}
    monkeypatch.setattr(housing, "_read", lambda path: files.get(path, ""))
    assert housing._host_class() == "desktop"  # noqa: SLF001
    files["/sys/class/dmi/id/product_name"] = "MacBookPro16,1"
    assert housing._host_class() == "laptop"  # noqa: SLF001
    files["/sys/class/dmi/id/product_name"] = "ThinkPad X1"
    assert housing._host_class() == "laptop"  # noqa: SLF001  (chassis 9 = laptop elsewhere)


def test_the_whole_report_is_capped_at_three_seconds(monkeypatch):
    """Boss (0.7.6): one attempt, <= 3 s in all. A server that never answers: the report gives up by
    _TIMEOUT even counting the one fresh-token POST, which only happens after a 401 anyway."""
    import socket
    import time

    assert housing._TIMEOUT <= 3.0
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(2)
    monkeypatch.setenv("ETERNITAS_URL", f"http://127.0.0.1:{srv.getsockname()[1]}")
    monkeypatch.setattr("windyfly.agent.service_auth.agent_headers", lambda aud, m, u: {"Authorization": "x"})
    t0 = time.monotonic()
    assert housing.report_once() == "unreachable"
    assert time.monotonic() - t0 < 3.5
    srv.close()
