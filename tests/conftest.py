"""Test-suite-wide autouse fixtures.

This file does TWO things for every test (autouse):

  1. **Isolates all production file-flag paths** to a per-test
     temp dir. Pre-conftest, tests using ``Database(":memory:")``
     would still see the live bot's ``~/.windy/.paused`` /
     ``.resurrected`` / ``.auto_resurrect_last`` etc. flags.
     This caused 21+ flaky failures in the 2026-05-07 hardening
     sweep when auto-resurrect fired on the live bot mid-test.

  2. **Disables the first-contact welcome shortcut by default**.
     PR #142's welcome short-circuits agent_respond on virgin DBs
     BEFORE the LLM call — saves a token in production but breaks
     unit tests that mock call_llm. Tests that specifically test
     first-contact behavior opt-in via
     ``@pytest.mark.virgin_db_welcome`` (or module-level
     ``pytestmark = pytest.mark.virgin_db_welcome``).

The autouse design guarantees no test sees production state and no
test gets bypassed by a feature shortcut by accident — the failure
modes ride exactly where the test author wrote them.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from windyfly.memory.write_queue import WriteQueue


@pytest.fixture
def wq():
    """A started-on-demand WriteQueue that is ALWAYS stopped.

    Was duplicated verbatim (``return WriteQueue()``, no teardown) in
    four test modules. Without the teardown a test that calls
    ``wq.start()`` leaks a daemon worker thread that outlives the test
    and keeps polling its queue forever.

    That leak is what turned ``TestWriteFailureTelemetry::
    test_failed_write_increments_stats`` into a flake: write-failure
    telemetry is deliberately PROCESS-WIDE (``_write_stats`` — so
    ``/status`` can surface "memory writes failing" no matter which
    queue hit the error), so a leaked worker processing its failing
    item late lands an extra increment inside a later test's window.
    Red master on CI 2026-07-23 (``assert 2 == 1``, Python 3.14 —
    3.12 happened to schedule it the other way).

    ``stop()`` drains before exiting (``while self._running or not
    self._queue.empty()``), so teardown guarantees every enqueued item
    is accounted for before the next test starts. The product itself
    was never double-counting — verified 200/200 trials with exact
    counts.
    """
    q = WriteQueue()
    yield q
    q.stop()


@pytest.fixture
def short_tmp_path():
    """A temp dir short enough to hold an AF_UNIX socket path.

    ``sockaddr_un.sun_path`` is 104 bytes on macOS/BSD (108 on Linux)
    — a hard kernel limit, not a tunable. pytest's ``tmp_path`` on a
    Mac expands to something like
    ``/private/var/folders/2h/xz_hfzq90t71_.../pytest-of-<user>/
    pytest-N/test_payload_arrives_at_unix_s0/`` which blows the limit
    before a filename is even appended, so ``bind()`` raises
    ``OSError: AF_UNIX path too long``.

    That is a property of the RUNNER's temp layout, not of the code
    under test — the same tests pass on CI's Linux box and failed only
    on Macs, which made "is the suite green?" ambiguous for anyone
    auditing on a laptop. Rooting the dir at ``/tmp`` keeps the whole
    path near 25 chars on every platform.
    """
    import shutil
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp(dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_production_flags(monkeypatch, tmp_path):
    """Redirect every production flag/marker file path to per-test
    tmp dirs. Without this, tests pick up state from earlier test
    runs OR from the live windy-0 bot that's running on the same
    machine.

    Surfaced 2026-05-07: live bot's auto-resurrect flag (actor=
    auto-chain-exhausted) leaked into test runs and routed mocked
    agent_respond calls through the resurrection-mode Ollama path
    instead of the LLM mock.
    """
    monkeypatch.setenv("WINDY_PAUSE_FLAG", str(tmp_path / ".paused"))
    monkeypatch.setenv("WINDY_YOLO_FLAG", str(tmp_path / ".yolo"))
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(tmp_path / ".guest"))
    monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".resurrected"))
    monkeypatch.setenv("WINDY_AUTO_RESURRECT_DISABLED",
                       str(tmp_path / ".auto_resurrect_disabled"))
    monkeypatch.setenv("WINDY_AUTO_RESURRECT_LAST",
                       str(tmp_path / ".auto_resurrect_last"))
    monkeypatch.setenv("WINDY_RECOVERY_PROBE_LAST",
                       str(tmp_path / ".recovery_probe_last"))
    monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE",
                       str(tmp_path / ".post_recovery_grace"))
    monkeypatch.setenv("WINDY_DAILY_SEARCH_COUNTER",
                       str(tmp_path / ".daily_search_count"))
    # Base state dir (2026-07-04): windy_state_dir() derives every flag
    # default AND holds provider-cooldowns.json + update-history.jsonl —
    # keep all of it out of the real ~/.windy on dev/prod machines.
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / ".windy-state"))
    # Provider overrides (2026-07-30): the dashboard writes a providers
    # file holding REAL api_keys. Its default used to be the cwd-relative
    # "data/providers.json", resolved at import, so on a standing checkout
    # that had one — OC5 again — the suite loaded a live OpenAI key and
    # three tests failed there and nowhere else. Same failure class as the
    # .env bleed handled just below. Pin it explicitly: when set, the
    # legacy cwd-relative file is not consulted at all.
    monkeypatch.setenv("WINDYFLY_PROVIDERS_PATH",
                       str(tmp_path / ".windy-state" / "providers.json"))
    # Claude Code credentials (2026-07-30): models._reload_oauth_token()
    # reads this file and writes the token it finds into os.environ —
    # process-global, so it survives every later test. On any fleet box the
    # credential-sync cron drops a REAL token at ~/.claude/.credentials.json,
    # so one auth-failure path pulled a live billable key into the suite and
    # two lifeboat "paid is unreachable" tests failed there and nowhere else.
    # Point it at a path that does not exist.
    monkeypatch.setenv("WINDY_CLAUDE_CREDENTIALS_PATH",
                       str(tmp_path / ".no-claude-credentials.json"))
    # Correction distillation makes a real LLM call in production;
    # tests always use the deterministic template path.
    monkeypatch.setenv("WINDY_LLM_CORRECTIONS", "0")
    # An EPT in the host env would make the Mind broker fire real HTTP
    # from any unmocked call_llm test (Sprint 5) — never inherit it.
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    # Neutralize load_dotenv() suite-wide (2026-07-18): several modules
    # call bare load_dotenv(), which walks up from CWD and loads a
    # resident repo-root .env. On a machine where windy-agent is a
    # standing checkout WITH a populated .env (e.g. OC5 Mac), that bled
    # real provider keys into the suite — flipping
    # test_unconfigured_provider_is_skipped_silently AND firing a live
    # Anthropic call mid-test (a "no money in tests" violation). Tests
    # get their env from fixtures/monkeypatch, never a file on disk.
    #
    # v2 (2026-07-18, fleet-caught): the first fix patched only
    # windyfly.config's imported alias — but 7 other call sites import
    # load_dotenv (cli.py x3, cli_selftest, main, bridge/uds_server,
    # hatching, setup_wizard). Neutralize it EVERYWHERE:
    #  (1) at the source (dotenv.load_dotenv) — catches every LAZY
    #      `from dotenv import load_dotenv` inside a function, which
    #      re-resolves the name at call time; and
    #  (2) on any already-imported windyfly module that captured a
    #      MODULE-LEVEL alias at import time (e.g. main.py) — done
    #      dynamically so a new call site can't silently reopen the hole.
    _noop_dotenv = lambda *a, **k: False  # noqa: E731
    import dotenv as _dotenv
    monkeypatch.setattr(_dotenv, "load_dotenv", _noop_dotenv, raising=False)
    for _name, _mod in list(sys.modules.items()):
        if _name.startswith("windyfly.") and getattr(_mod, "load_dotenv", None):
            monkeypatch.setattr(_mod, "load_dotenv", _noop_dotenv, raising=False)
    yield


@pytest.fixture(autouse=True)
def _no_real_process_kills(request):
    """Block the pkill fall-through for every test by default.

    ``cli.cmd_stop`` falls back to ``kill_by_name(["windyfly.main", ...])``
    when no PID file matches — and on a machine running a live agent that
    pattern kills the PRODUCTION process (observed 2026-07-04: running the
    suite on Windy 0 stopped windy-0.service). Tests that genuinely
    exercise process-kill behavior opt in via the ``real_process_kill``
    marker; direct tests of ``windyfly.platform`` functions are unaffected
    (we patch cli's imported reference, not the platform module).
    """
    if "real_process_kill" in request.keywords:
        yield
        return
    # rescue.schedule_restart SIGTERMs the running process (the /reset
    # panic path) — under pytest that's the test runner itself.
    with patch("windyfly.cli.kill_by_name", return_value=None), \
         patch("windyfly.channels.rescue.schedule_restart"):
        yield


@pytest.fixture(autouse=True)
def _isolate_project_root(request, monkeypatch, tmp_path):
    """Point every module-level PROJECT_ROOT copy at a per-test tmp dir.

    Five modules snapshot ``get_project_root()`` at import time
    (cli, quickstart, setup_wizard, commands.core, commands._legacy);
    isolation used to be opt-in per test, and the omission failure mode
    was real: test_pro_broker's quickstart test OVERWROTE the repo's
    windyfly.toml/.env with fixture config (observed 2026-07-04 on 0c2).
    Tests that need the real repo root (e.g. source-scanning tests that
    use PROJECT_ROOT rather than __file__) opt in via the
    ``real_project_root`` marker.
    """
    if "real_project_root" in request.keywords:
        yield
        return
    import importlib

    root = tmp_path / "project-root"
    (root / "data").mkdir(parents=True, exist_ok=True)
    for mod_name in (
        "windyfly.cli",
        "windyfly.quickstart",
        "windyfly.setup_wizard",
        "windyfly.commands.core",
        "windyfly.commands._legacy",
    ):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, "PROJECT_ROOT"):
            monkeypatch.setattr(mod, "PROJECT_ROOT", root)
        # setup_wizard derives file paths from PROJECT_ROOT at import.
        for attr, rel in (
            ("ENV_FILE", ".env"),
            ("CONFIG_FILE", "windyfly.toml"),
            ("DATA_DIR", "data"),
        ):
            if hasattr(mod, attr):
                monkeypatch.setattr(mod, attr, root / rel)
    yield


@pytest.fixture(autouse=True)
def _default_skip_first_contact_welcome(request):
    """Default OFF for the first-contact welcome shortcut so unit
    tests that mock call_llm aren't bypassed.

    Tests that need real first-contact behavior opt-in via the
    ``virgin_db_welcome`` marker. ``test_first_contact_welcome.py``
    already does this at the module level::

        pytestmark = pytest.mark.virgin_db_welcome
    """
    if "virgin_db_welcome" in request.keywords:
        # Test explicitly wants the real welcome behavior — let it
        # through.
        yield
        return
    # Default: prevent welcome from firing.
    with patch("windyfly.agent.welcome.is_first_contact", return_value=False):
        yield


@pytest.fixture(autouse=True)
def _default_skip_real_ollama(request):
    """Default OFF for real Ollama calls. Once Ollama is installed
    on the host (PR #148), tests that don't explicitly mock the
    Ollama probe path otherwise hit the real local server, with
    30s timeouts per call. A test file with 10 LLM-mock tests that
    each fall through to chain-exhaustion would take 300+ seconds.

    Tests that EXERCISE the Ollama integration opt-in via the
    ``real_ollama`` marker.
    """
    if "real_ollama" in request.keywords:
        yield
        return
    with patch("windyfly.agent.offline.is_ollama_available", return_value=False), \
         patch("windyfly.agent.resurrect.list_installed_ollama_models", return_value=[]):
        yield


@pytest.fixture(autouse=True)
def _default_skip_state_emoji_prefix(request):
    """Default OFF for the gas-tank panel + always-on state emoji
    prefix (PR #144) so unit tests that check exact LLM-mock output
    don't have to account for header bytes that were never the
    behavior they were testing.

    Tests that EXERCISE the prefix behavior (test_context_header.py,
    test_context_header_per_session.py) opt-in via the
    ``state_emoji_prefix`` marker.
    """
    if "state_emoji_prefix" in request.keywords:
        yield
        return
    # Default: identity-passthrough. The agent loop calls
    # maybe_prepend_header(text, tokens) and expects a string back —
    # make it the original text untouched.
    with patch(
        "windyfly.agent.context_header.maybe_prepend_header",
        side_effect=lambda text, tokens, max_tokens=200_000, **kw: text,
    ), patch(
        "windyfly.agent.loop.maybe_prepend_header",
        side_effect=lambda text, tokens, max_tokens=200_000, **kw: text,
    ):
        yield


@pytest.fixture(autouse=True)
def _allow_fake_identity_in_tests(monkeypatch):
    """Let the suite reach the local Eternitas mock.

    `get_eternitas_client` refuses to fall back to `MockEternitasClient`
    on an unconfigured run, because doing so silently minted passport
    numbers Eternitas had never issued while the ceremony reported
    success. Tests are a legitimate consumer of the mock, so they opt in
    the same way an offline developer does — explicitly.

    Tests that exercise the refusal itself just `monkeypatch.delenv` it;
    fixtures run before the test body, so the delete wins.
    """
    from windyfly.eternitas.provision import FAKE_IDENTITY_OPTIN_ENV

    monkeypatch.setenv(FAKE_IDENTITY_OPTIN_ENV, "1")
