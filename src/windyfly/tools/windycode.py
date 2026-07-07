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
        path = _socket_path()
        if sys.platform != "win32" and not os.path.exists(path):
            raise WindyCodeUnavailable(
                "Windy Code doesn't appear to be running (no agent socket). "
                "Ask the user to open Windy Code, then try again."
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
        self._authenticate()
        return self

    def __exit__(self, *exc: Any) -> None:
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
    projects = sorted(p.name for p in root.iterdir() if p.is_dir())
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
                "git -c user.name='Windy Fly' -c user.email='agent@windymail.ai' "
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
                f"git -c user.name='Windy Fly' -c user.email='agent@windymail.ai' "
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


def register_windycode_tools(registry: ToolRegistry) -> None:
    """Register the Windy Code Agent Bus tools."""
    _project_arg = {
        "type": "string",
        "description": "The project name (a simple slug like 'bake-sale-website').",
    }

    registry.register(
        name="windycode_status",
        description=(
            "Check whether Windy Code (the user's IDE) is open and reachable. "
            "Call this first if a windycode_* tool returns 'unavailable'. "
            "Returns {status: connected|unavailable|error}."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=windycode_status,
    )

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
