"""Windy Code tools — let the agent build the user's projects inside Windy Code.

Windy Code (the Windy ecosystem's IDE) exposes a local **Agent Bus**: a Unix
domain socket that speaks JSON-per-line. A hatched Windy Fly agent authenticates
with its Eternitas passport and can then drive the editor, terminal, and git —
the same actions a human could take in the IDE. This module wraps that bus as
LLM-callable tools so, when the user says "build me a website", the agent can
scaffold it *in Windy Code* (where the user keeps and sees all their projects)
instead of writing files into random directories.

Design decisions:
  * **Project-home convention.** Every project lives under
    ``~/grandma-projects/<name>`` — which is the folder Windy Code opens as its
    workspace, so the files show up in the user's IDE. The agent NEVER writes to
    a relative path (that lands wherever the agent process happens to be — a
    real bug we hit before). Paths are always resolved under the projects dir
    and validated to stay inside it.
  * **Terminal as the workhorse.** File writes / git run through
    ``windy.terminal.run`` with an explicit ``cwd`` (the Agent Bus git commands
    take no per-call cwd). File content is base64-encoded on the wire so
    arbitrary content — quotes, newlines, unicode — round-trips safely.
  * **Editor for visibility.** After writing a file we ``windy.editor.openFile``
    it so it appears in the user's editor. Best-effort: if no window is active
    the bus now returns a clean error (it used to hang), which we swallow.
  * **Graceful when Windy Code is closed.** If the socket is absent we return a
    structured ``unavailable`` result the LLM can relay ("open Windy Code
    first") rather than throwing.

Environment:
    WINDYCODE_AGENT_SOCK   — socket path (default /tmp/windycode-agent.sock;
                             a named pipe on Windows)
    WINDYCODE_PROJECTS_DIR — projects home (default ~/grandma-projects)
    ETERNITAS_PASSPORT     — the agent's passport id (auth)
    ETERNITAS_PASSPORT_TOKEN / WINDY_JWT — the EPT presented as the bus credential
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shlex
import socket
import sys
import uuid
from pathlib import Path
from typing import Any

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 5.0
_COMMAND_TIMEOUT = 45.0


# ─── Agent Bus client ────────────────────────────────────────────────


class WindyCodeUnavailable(Exception):
    """Raised when Windy Code's Agent Bus socket isn't reachable."""


def _socket_path() -> str:
    env = os.environ.get("WINDYCODE_AGENT_SOCK", "")
    if env:
        return env
    if sys.platform == "win32":
        return r"\\.\pipe\windycode-agent"
    return "/tmp/windycode-agent.sock"


def _git_identity_args() -> str:
    """``-c user.name=... -c user.email=...`` from the agent's real identity (its own mailbox)."""
    from windyfly.tools import outbound_identity

    name, email = outbound_identity.git_identity()
    return f"-c user.name={shlex.quote(name)} -c user.email={shlex.quote(email)}"


def _projects_dir() -> Path:
    return Path(os.environ.get("WINDYCODE_PROJECTS_DIR", "")
                or (Path.home() / "grandma-projects")).expanduser()


def _auth_credentials() -> tuple[str, str]:
    passport = os.environ.get("ETERNITAS_PASSPORT", "")
    token = (
        os.environ.get("ETERNITAS_PASSPORT_TOKEN", "")
        or os.environ.get("WINDY_JWT", "")
    )
    return passport, token


# ── Mode B on the IDE agent bus (DARK behind WINDY_CODE_BUS_EPT_AGENT; Windy Code's IDE side is due Mon 11-09) ──
#
# Code's contract (11-04, Eternitas ruled): the auth message carries the agent's EPT+agent (aud windy-code) as
# windy_jwt plus a proof in "dpop" (htm AUTH, htu "urn:windycode:agent-bus" byte for byte, fresh per connection). A
# refusal is {"status":"error","error":<text>,"code":<reason>}: key on "code" only. One auth per connection: the bus
# closes after any auth error, so every retry is a NEW connection. Only wrong_token_type (the IDE's mode B is off)
# falls back to the stored EPT; proof codes get one new proof, token codes one new mint, and every other code STOPS.
BUS_FLAG = "WINDY_CODE_BUS_EPT_AGENT"
BUS_AUD = "windy-code"
BUS_HTM = "AUTH"
BUS_HTU = "urn:windycode:agent-bus"
_BUS_FALLBACK = "wrong_token_type"
_BUS_FRESH_PROOF = frozenset({"dpop_required", "invalid_dpop", "dpop_replay"})
_BUS_REMINT = frozenset({"wrong_audience", "expired", "invalid_token"})
_BUS_STOP_SAY = {
    "passport_revoked": "Windy Code refused this agent: its Eternitas passport is revoked.",
    "passport_suspended": "Windy Code refused this agent: its Eternitas passport is suspended.",
    "ept_cutoff": "Windy Code refused this agent's old passport token, and it has no newer one.",
    "standing_unavailable": "Windy Code couldn't check this agent's standing just now. Try again in a few minutes.",
    "too_many_connections": "Windy Code has too many agent connections open. Try again when one finishes.",
}


def bus_mode_b_enabled() -> bool:
    return os.environ.get(BUS_FLAG, "").strip() == "1"


def _bus_agent_auth() -> dict[str, str] | None:
    """The mode-B auth message (EPT+agent + a FRESH proof), or None when none can be made (then the stored EPT)."""
    try:
        from windyfly.eternitas import agent_keys as ak

        token = ak.request_agent_token(BUS_AUD)["token"]
        passport = os.environ.get("ETERNITAS_PASSPORT", "").strip() or str(ak.current_passport() or "")
        if not passport:
            return None
        return {"type": "auth", "eternitas_passport": passport, "windy_jwt": token,
                "dpop": ak.bus_dpop(BUS_HTM, BUS_HTU)}
    except Exception as exc:  # noqa: BLE001  (AgentTokenError, no key, network)
        logger.info("[code-bus-auth] no EPT+agent for windy-code (%s); stored EPT",
                    getattr(exc, "code", type(exc).__name__))
        return None


class _BusConnection:
    """One authenticated request/response round-trip over the Agent Bus.

    The bus caps concurrent connections and rate-limits commands, so we open a
    fresh connection per logical operation and close it — simple and robust for
    tool-call frequency. Use as a context manager.
    """

    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._buf = b""

    def __enter__(self) -> "_BusConnection":
        self._connect()
        self._authenticate()
        return self

    def _connect(self) -> None:
        self._close()
        self._buf = b""
        path = _socket_path()
        if sys.platform != "win32":
            import stat as _stat

            try:
                st = os.stat(path)
            except OSError:
                raise WindyCodeUnavailable(
                    "Windy Code doesn't appear to be running (no agent socket). "
                    "Ask the user to open Windy Code, then try again."
                ) from None
            # Checked on EVERY connect, not only at boot: the default path is in a shared /tmp, and after this
            # user's IDE exits another user could put a socket there (Hub on #644).
            if not _stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
                raise WindyCodeUnavailable(
                    "The Windy Code agent socket here doesn't belong to this user, so it wasn't used. "
                    "Ask the user to open their own Windy Code, then try again."
                )
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(_CONNECT_TIMEOUT)
        try:
            s.connect(path)
        except OSError as e:
            raise WindyCodeUnavailable(
                f"Couldn't connect to Windy Code's agent socket: {e}. "
                "Ask the user to open Windy Code."
            ) from e
        self._sock = s

    def __exit__(self, *exc: Any) -> None:
        self._close()

    def _close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _send(self, obj: dict[str, Any]) -> None:
        assert self._sock is not None
        self._sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))

    def _recv(self) -> dict[str, Any]:
        assert self._sock is not None
        self._sock.settimeout(_COMMAND_TIMEOUT)
        while b"\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise WindyCodeUnavailable("Windy Code closed the connection.")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return json.loads(line.decode("utf-8"))

    def _authenticate(self) -> None:
        if not bus_mode_b_enabled():
            self._authenticate_stored()
            return
        msg = _bus_agent_auth()
        if msg is None:
            self._authenticate_stored()
            return
        resp = self._auth_once(msg)
        code = str(resp.get("code") or "").strip().lower() if resp.get("status") != "ok" else ""
        if code in _BUS_FRESH_PROOF or code in _BUS_REMINT:
            if code in _BUS_REMINT:
                from windyfly.agent import service_auth

                service_auth.forget_token()
            again = _bus_agent_auth()  # always a new proof (one auth per connection, a jti is single-use)
            if again is not None:
                self._connect()
                resp = self._auth_once(again)
                code = str(resp.get("code") or "").strip().lower() if resp.get("status") != "ok" else ""
        if resp.get("status") == "ok":
            return
        # No "code" at all = an IDE from before mode B (every desktop until it updates past Code's 11-09 build; a
        # desktop release needs Grant's typed "release", and people update late): today's path, so fall back like
        # wrong_token_type. A closed connection with no answer is the same. A code we don't know still STOPS.
        if (code == _BUS_FALLBACK or not code) and _auth_credentials()[1]:
            logger.info("[code-bus-auth] %s: the IDE's mode B is off; a new connection with the stored EPT",
                        code or "no code (an IDE from before mode B)")
            self._connect()
            self._authenticate_stored()
            return
        logger.info("[code-bus-auth] refused (%s); not falling back", code or "no code")
        raise WindyCodeUnavailable(_BUS_STOP_SAY.get(code) or
                                   f"Windy Code rejected the agent's credentials: {resp.get('error', 'unknown error')}")

    def _auth_once(self, msg: dict[str, str]) -> dict[str, Any]:
        self._send(msg)
        try:
            return self._recv()
        except (WindyCodeUnavailable, OSError):  # closed or no answer: no "code", like an IDE from before mode B
            return {"status": "error", "error": "closed"}

    def _authenticate_stored(self) -> None:
        passport, token = _auth_credentials()
        if not passport or not token:
            raise WindyCodeUnavailable(
                "This agent has no Eternitas passport configured, so it can't "
                "authenticate to Windy Code."
            )
        self._send({"type": "auth", "eternitas_passport": passport, "windy_jwt": token})
        resp = self._recv()
        if resp.get("status") != "ok":
            raise WindyCodeUnavailable(
                f"Windy Code rejected the agent's credentials: {resp.get('error', 'unknown error')}"
            )

    def command(self, namespace: str, command: str, args: dict[str, Any] | None = None) -> Any:
        """Run one Agent Bus command; return its result or raise on error."""
        req_id = uuid.uuid4().hex[:8]
        self._send({"id": req_id, "namespace": namespace, "command": command, "args": args or {}})
        resp = self._recv()
        if resp.get("status") != "ok":
            raise RuntimeError(resp.get("error", f"{namespace}.{command} failed"))
        return resp.get("result")


def _run_shell(conn: _BusConnection, command: str, cwd: str) -> dict[str, Any]:
    """Run a shell command in the IDE terminal and return its structured result."""
    result = conn.command("windy.terminal", "run", {"command": command, "cwd": cwd})
    if not isinstance(result, dict):
        return {"exitCode": 0, "stdout": str(result), "stderr": ""}
    return result


# ─── Project-home helpers ────────────────────────────────────────────


def _safe_project_dir(name: str) -> Path:
    """Resolve a project directory under the projects home, rejecting escapes.

    A project name must be a single path segment: no slashes, no traversal, no
    leading dot. Anything else is rejected outright (not coerced) so a name like
    '/etc' or '../x' can never resolve outside the projects home.
    """
    root = _projects_dir().resolve()
    clean = name.strip()
    if (not clean or "/" in clean or "\\" in clean
            or clean in (".", "..") or clean.startswith(".")):
        raise ValueError(
            f"Invalid project name {name!r}. Use a simple name like 'bake-sale-website'."
        )
    target = (root / clean).resolve()
    if target.parent != root:
        raise ValueError(f"Invalid project name {name!r}.")
    return target


def _safe_file_path(project: str, rel_path: str) -> tuple[Path, Path]:
    """Resolve (project_dir, absolute_file_path), rejecting any escape."""
    proj = _safe_project_dir(project)
    target = (proj / rel_path.lstrip("/")).resolve()
    if proj != target and proj not in target.parents:
        raise ValueError(
            f"Refusing to write outside the project folder: {rel_path!r}."
        )
    return proj, target


# ─── Tools ───────────────────────────────────────────────────────────


def windycode_status() -> dict[str, Any]:
    """Check whether Windy Code is open and the agent can reach it."""
    try:
        with _BusConnection() as conn:
            conn.command("windy.terminal", "run", {"command": "true", "cwd": str(_projects_dir())})
        return {"status": "connected", "projects_dir": str(_projects_dir())}
    except WindyCodeUnavailable as e:
        return {"status": "unavailable", "message": str(e)}
    except Exception as e:  # noqa: BLE001 — surface anything else to the LLM
        return {"status": "error", "message": str(e)}


def windycode_list_projects() -> dict[str, Any]:
    """List the user's projects in Windy Code."""
    root = _projects_dir()
    if not root.exists():
        return {"status": "ok", "projects": [], "note": "No projects yet."}
    projects = sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )
    return {"status": "ok", "projects": projects, "count": len(projects)}


def windycode_create_project(name: str, description: str = "") -> dict[str, Any]:
    """Create a new project folder in Windy Code (with git) and open it."""
    try:
        proj = _safe_project_dir(name)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    root = _projects_dir()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with _BusConnection() as conn:
            script = (
                f"mkdir -p {shlex.quote(str(proj))} && cd {shlex.quote(str(proj))} && "
                "if [ ! -d .git ]; then git init -b main -q && "
                f"git {_git_identity_args()} "
                "commit --allow-empty -qm 'start of project' ; fi && echo created"
            )
            res = _run_shell(conn, script, str(root))
            if res.get("exitCode") != 0:
                return {"status": "error", "message": res.get("stderr") or "could not create project"}
    except WindyCodeUnavailable as e:
        return {"status": "unavailable", "message": str(e)}
    return {
        "status": "ok",
        "project": name,
        "path": str(proj),
        "message": f"Created project '{name}' in Windy Code.",
    }


def windycode_write_file(project: str, path: str, content: str) -> dict[str, Any]:
    """Write (create or overwrite) a file inside a Windy Code project and open it."""
    try:
        proj, target = _safe_file_path(project, path)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    if not proj.exists():
        return {"status": "error", "message": f"Project '{project}' doesn't exist — create it first."}
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    try:
        with _BusConnection() as conn:
            script = (
                f"mkdir -p {shlex.quote(str(target.parent))} && "
                f"printf %s {shlex.quote(b64)} | base64 -d > {shlex.quote(str(target))} && echo wrote"
            )
            res = _run_shell(conn, script, str(proj))
            if res.get("exitCode") != 0:
                return {"status": "error", "message": res.get("stderr") or "write failed"}
            # Best-effort: surface the file in the user's editor.
            try:
                conn.command("windy.editor", "openFile", {"path": str(target)})
            except Exception:  # noqa: BLE001 — no active window is fine
                pass
    except WindyCodeUnavailable as e:
        return {"status": "unavailable", "message": str(e)}
    return {
        "status": "ok",
        "path": str(target),
        "bytes": len(content.encode("utf-8")),
        "message": f"Wrote {path} in project '{project}' (open in Windy Code).",
    }


def windycode_read_file(project: str, path: str) -> dict[str, Any]:
    """Read a file from a Windy Code project."""
    try:
        proj, target = _safe_file_path(project, path)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    try:
        with _BusConnection() as conn:
            res = _run_shell(conn, f"cat {shlex.quote(str(target))}", str(proj))
            if res.get("exitCode") != 0:
                return {"status": "error", "message": res.get("stderr") or "read failed"}
            return {"status": "ok", "path": str(target), "content": res.get("stdout", "")}
    except WindyCodeUnavailable as e:
        return {"status": "unavailable", "message": str(e)}


def windycode_run(project: str, command: str) -> dict[str, Any]:
    """Run a terminal command inside a Windy Code project."""
    try:
        proj = _safe_project_dir(project)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    if not proj.exists():
        return {"status": "error", "message": f"Project '{project}' doesn't exist — create it first."}
    try:
        with _BusConnection() as conn:
            res = _run_shell(conn, command, str(proj))
    except WindyCodeUnavailable as e:
        return {"status": "unavailable", "message": str(e)}
    return {
        "status": "ok",
        "exitCode": res.get("exitCode"),
        "stdout": res.get("stdout", ""),
        "stderr": res.get("stderr", ""),
    }


def windycode_save_to_git(project: str, message: str) -> dict[str, Any]:
    """Save all changes in a Windy Code project to git (add + commit)."""
    try:
        proj = _safe_project_dir(project)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    if not (proj / ".git").exists():
        return {"status": "error", "message": f"Project '{project}' isn't a git repo yet."}
    try:
        with _BusConnection() as conn:
            script = (
                "git add -A && "
                f"git {_git_identity_args()} "
                f"commit -m {shlex.quote(message)} 2>&1 | tail -1"
            )
            res = _run_shell(conn, script, str(proj))
            out = (res.get("stdout") or "").strip()
            if res.get("exitCode") != 0 and "nothing to commit" not in out:
                return {"status": "error", "message": out or "commit failed"}
            head = _run_shell(conn, "git rev-parse --short HEAD", str(proj))
    except WindyCodeUnavailable as e:
        return {"status": "unavailable", "message": str(e)}
    return {
        "status": "ok",
        "commit": (head.get("stdout") or "").strip(),
        "message": f"Saved '{project}' to git: {out}",
    }


# ─── Registration ────────────────────────────────────────────────────


def _socket_live(path: str, timeout: float = 0.5) -> bool:
    """Is THIS user's IDE listening on this Unix socket? A file left behind by an IDE that has exited is not (10-21:
    a stale /tmp/windycode-agent.sock registered the Windy Code tools on every agent on the host). The default path
    is in a shared /tmp: a live socket owned by ANOTHER user is someone else's IDE and never counts (Hub on #644)."""
    import socket
    import stat

    try:
        st = os.stat(path)
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path)
        return True
    except OSError:
        return False


def available() -> bool:
    """The IDE's Agent Bus is configured (WINDYCODE_AGENT_SOCK) or its default socket is LIVE at boot (something
    answers on it); a leftover socket file with nothing listening doesn't count."""
    if os.environ.get("WINDYCODE_AGENT_SOCK"):
        return True
    path = _socket_path()
    if sys.platform == "win32":
        # The named pipe is per session on Windows; no owner check is made here (no portable pipe-owner API).
        return os.path.exists(path)
    return _socket_live(path)


def register_windycode_tools(registry: ToolRegistry) -> None:
    """Register the Windy Code Agent Bus tools, only where the desktop IDE is (principle 4: register only
    when configured). Started the IDE after the agent? The tools appear on the agent's next start."""
    if not available():
        return
    _project_arg = {
        "type": "string",
        "description": "The project name (a simple slug like 'bake-sale-website').",
    }

    # No windycode_status tool (tool trim phase 1): every bus-backed tool already answers
    # {status: 'unavailable', message} when the IDE is closed. windycode_status() stays for code.

    registry.register(
        name="windycode_list_projects",
        description=(
            "List the projects the user has in Windy Code. Use when they ask "
            "'what have I built?' or before creating a project to avoid a "
            "duplicate name."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=windycode_list_projects,
    )

    registry.register(
        name="windycode_create_project",
        description=(
            "Create a new project in Windy Code (a folder + git) and open it. "
            "Use this FIRST when the user asks you to build something new, "
            "before writing any files. Returns {status, path}."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": _project_arg,
                "description": {
                    "type": "string",
                    "description": "Optional one-line description of the project.",
                },
            },
            "required": ["name"],
        },
        fn=windycode_create_project,
    )

    registry.register(
        name="windycode_write_file",
        description=(
            "Create or overwrite a file inside a Windy Code project, and open "
            "it in the user's editor so they can see it. Use for every file you "
            "build (index.html, style.css, app.py, …). Content is written "
            "exactly as given."
        ),
        parameters={
            "type": "object",
            "properties": {
                "project": _project_arg,
                "path": {
                    "type": "string",
                    "description": "File path within the project, e.g. 'index.html' or 'src/app.py'.",
                },
                "content": {
                    "type": "string",
                    "description": "The full file content to write.",
                },
            },
            "required": ["project", "path", "content"],
        },
        fn=windycode_write_file,
    )

    registry.register(
        name="windycode_read_file",
        description="Read a file from a Windy Code project. Returns {status, content}.",
        parameters={
            "type": "object",
            "properties": {
                "project": _project_arg,
                "path": {"type": "string", "description": "File path within the project."},
            },
            "required": ["project", "path"],
        },
        fn=windycode_read_file,
    )

    registry.register(
        name="windycode_run",
        description=(
            "Run a terminal command inside a Windy Code project (e.g. 'npm "
            "install', 'python app.py', 'ls'). Runs in the project folder. "
            "Returns {status, exitCode, stdout, stderr}."
        ),
        parameters={
            "type": "object",
            "properties": {
                "project": _project_arg,
                "command": {"type": "string", "description": "The shell command to run."},
            },
            "required": ["project", "command"],
        },
        fn=windycode_run,
    )

    registry.register(
        name="windycode_save_to_git",
        description=(
            "Save all changes in a Windy Code project to git (add + commit) so "
            "nothing is lost. Use after finishing a set of changes. Returns "
            "{status, commit}."
        ),
        parameters={
            "type": "object",
            "properties": {
                "project": _project_arg,
                "message": {
                    "type": "string",
                    "description": "A short description of what changed (the commit message).",
                },
            },
            "required": ["project", "message"],
        },
        fn=windycode_save_to_git,
    )
