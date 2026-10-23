"""scripts/fire-drill.sh emit(): the Admin ingest token never rides in argv (Hub sweep 10-23: /proc has no hidepid, so
every local account can read a running command's arguments with ps). It reaches the server as the Authorization header
all the same. A curl shim on PATH logs its argv and then runs the real curl against a local server."""

import http.server
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fire-drill.sh"
TOKEN = "fake-ingest-token-7f3a9c"


def _emit_fn() -> str:
    lines, out = SCRIPT.read_text().splitlines(), []
    for line in lines:
        if out or line.startswith("emit() {"):
            out.append(line)
            if line == "}":
                break
    assert out and out[-1] == "}"
    return "\n".join(out)


@pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
def test_emit_sends_the_token_as_a_header_but_never_in_argv(tmp_path):
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen["auth"] = self.headers.get("Authorization")
            seen["synthetic"] = self.headers.get("X-Windy-Synthetic")
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(202)
            self.end_headers()
            self.wfile.write(b'{"accepted":1,"quarantined":0}')

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    b = tmp_path / "bin"
    b.mkdir()
    argv_log = tmp_path / "argv.log"
    shim = b / "curl"
    shim.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" >> {argv_log}\nexec {shutil.which("curl")} "$@"\n'
    )
    shim.chmod(0o755)
    script = (
        f"{_emit_fn()}\nWINDY_ADMIN_INGEST_URL=http://127.0.0.1:{srv.server_port}/ "
        f"WINDY_ADMIN_INGEST_TOKEN={TOKEN} emit backup pass 12\n"
    )
    try:
        r = subprocess.run(
            ["bash", "-c", script],
            env={"PATH": f"{b}:/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=20,
        )
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert seen.get("auth") == f"Bearer {TOKEN}" and seen.get("synthetic") == "1"
    argv = argv_log.read_text()
    assert argv and TOKEN not in argv and "Authorization" not in argv


def test_emit_is_silent_without_the_ingest_env(tmp_path):
    argv_log = tmp_path / "argv.log"
    b = tmp_path / "bin"
    b.mkdir()
    (b / "curl").write_text(f"#!/usr/bin/env bash\necho called >> {argv_log}\n")
    (b / "curl").chmod(0o755)
    r = subprocess.run(
        ["bash", "-c", f"{_emit_fn()}\nemit backup pass 1\n"],
        env={"PATH": f"{b}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert r.returncode == 0 and not argv_log.exists()


@pytest.mark.parametrize("bad", ['tok"en', "tok\\en", 'tok\nheader = "X: 1"', "tok en"])
def test_a_token_curl_config_cannot_carry_is_skipped_not_sent(tmp_path, bad):
    """A quote, backslash, newline or space would corrupt the -K config or inject a line: skip the report, warn,
    keep the drill going (Hub nit on #651)."""
    argv_log = tmp_path / "argv.log"
    b = tmp_path / "bin"
    b.mkdir()
    (b / "curl").write_text(f"#!/usr/bin/env bash\necho called >> {argv_log}\n")
    (b / "curl").chmod(0o755)
    r = subprocess.run(
        ["bash", "-c", f'{_emit_fn()}\nemit backup pass 1\necho "rc=$?"\n'],
        env={
            "PATH": f"{b}:/usr/bin:/bin",
            "WINDY_ADMIN_INGEST_URL": "http://127.0.0.1:9/",
            "WINDY_ADMIN_INGEST_TOKEN": bad,
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "rc=0" in r.stdout and "unexpected characters" in r.stderr and not argv_log.exists()
