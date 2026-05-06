"""Cloud tools — let the LLM upload to and list files on Windy Cloud.

Sprint 1.b extension. The agent already had ``cloud_backup.py`` for
encrypted DB backups, but no tool for "user said: save this file to
my cloud." This module exposes two LLM-callable tools that POST/GET
the Windy Cloud archive API directly.

Like the chat tool, this uses sync httpx (the existing cloud_backup
helper is async-first because it's called from the long-lived agent
loop; tools live in the sync registry path). Auth is a Bearer token
from env — for v0 we assume ``WINDY_CLOUD_TOKEN`` or fall back to
``WINDY_JWT`` (the agent's main credential).

Environment:
    WINDY_CLOUD_URL    — e.g. https://cloud.windyword.ai
    WINDY_CLOUD_TOKEN  — bearer for cloud (preferred). Falls back to
                          WINDY_JWT if cloud-specific token unset.

API contract assumed (Cloud's archive namespace):
    POST   {cloud_url}/api/v1/archive/file        — multipart upload
    GET    {cloud_url}/api/v1/archive/files        — list files
    GET    {cloud_url}/api/v1/archive/file/{id}    — download (not
                                                     wrapped here; v1)

The same scaffold-mode discipline applies: when env isn't set OR the
endpoint 404s (Cloud not yet deployed), the tool returns a structured
``unavailable`` / ``failed`` response the LLM can interpret.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_UPLOAD_TIMEOUT = 60.0   # uploads may be large
_LIST_TIMEOUT = 10.0


def _cloud_creds() -> tuple[str, str]:
    """Resolve (cloud_url, token). Empty strings indicate not configured."""
    url = os.environ.get("WINDY_CLOUD_URL", "").rstrip("/")
    token = (
        os.environ.get("WINDY_CLOUD_TOKEN", "")
        or os.environ.get("WINDY_JWT", "")
    )
    return url, token


def upload_to_cloud(
    file_path: str,
    name: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Upload a file from disk to Windy Cloud.

    The user's request is typically "save this to my cloud" or
    "upload <path> to cloud". The agent already has filesystem access
    via the capability plane, so it can resolve relative paths into
    absolute ones before calling this tool.
    """
    cloud_url, token = _cloud_creds()
    if not cloud_url or not token:
        return {
            "status": "unavailable",
            "error": (
                "Cloud is not configured for this agent. "
                "WINDY_CLOUD_URL and WINDY_CLOUD_TOKEN (or WINDY_JWT) "
                "must be set."
            ),
        }

    path = Path(file_path).expanduser()
    if not path.exists():
        return {"status": "failed", "error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"status": "failed", "error": f"Not a regular file: {file_path}"}

    target = f"{cloud_url}/api/v1/archive/file"
    upload_name = name.strip() or path.name
    data: dict[str, str] = {}
    if description.strip():
        data["description"] = description.strip()

    try:
        with path.open("rb") as fh:
            files = {"file": (upload_name, fh)}
            resp = httpx.post(
                target,
                files=files,
                data=data,
                headers={"Authorization": f"Bearer {token}"},
                timeout=_UPLOAD_TIMEOUT,
            )
    except httpx.ConnectError as exc:
        return {
            "status": "failed",
            "error": f"Cannot reach Windy Cloud at {cloud_url}: {exc}",
        }
    except httpx.HTTPError as exc:
        return {"status": "failed", "error": f"Cloud transport error: {exc}"}

    if resp.status_code in (200, 201):
        try:
            body = resp.json()
        except ValueError:
            body = {}
        return {
            "status": "uploaded",
            "name": upload_name,
            "size_bytes": path.stat().st_size,
            **body,
        }

    if resp.status_code == 404:
        # Cloud may be reachable but this endpoint not yet deployed.
        return {
            "status": "unavailable",
            "error": (
                f"Cloud archive endpoint not found at {target}. "
                "The Cloud service may not have shipped file uploads yet."
            ),
        }
    return {
        "status": "failed",
        "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
    }


def list_cloud_files(
    prefix: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """List files in Windy Cloud, optionally filtered by name prefix."""
    cloud_url, token = _cloud_creds()
    if not cloud_url or not token:
        return {
            "status": "unavailable",
            "files": [],
            "error": "Cloud is not configured for this agent.",
        }

    target = f"{cloud_url}/api/v1/archive/files"
    params: dict[str, Any] = {"limit": max(1, min(limit, 200))}
    if prefix.strip():
        params["prefix"] = prefix.strip()

    try:
        resp = httpx.get(
            target,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_LIST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        return {
            "status": "failed",
            "files": [],
            "error": f"Cloud list transport error: {exc}",
        }

    if resp.status_code == 200:
        try:
            body = resp.json()
        except ValueError:
            return {"status": "failed", "files": [], "error": "Non-JSON response"}
        # Cloud returns either {files: [...]} or a bare list — normalise.
        files = body.get("files") if isinstance(body, dict) else body
        if not isinstance(files, list):
            files = []
        return {
            "status": "ok",
            "files": files[:limit],
            "count": min(len(files), limit),
        }

    if resp.status_code == 404:
        return {
            "status": "unavailable",
            "files": [],
            "error": f"Cloud archive list endpoint not found at {target}.",
        }
    return {
        "status": "failed",
        "files": [],
        "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
    }


def register_cloud_tools(registry: ToolRegistry) -> None:
    """Register ``upload_to_cloud`` and ``list_cloud_files`` with the registry."""
    registry.register(
        name="upload_to_cloud",
        description=(
            "Upload a local file to the user's Windy Cloud storage. Use "
            "when the user says 'save this to cloud', 'upload <file> to "
            "my cloud', or when an agent task generates a document the "
            "user should keep. The file_path can be absolute or relative "
            "to the agent's cwd. Returns {status: 'uploaded', name, "
            "size_bytes, ...} on success or {status: 'unavailable' | "
            "'failed', error} otherwise."
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to upload.",
                },
                "name": {
                    "type": "string",
                    "description": "Optional display name in cloud (defaults to file basename).",
                },
                "description": {
                    "type": "string",
                    "description": "Optional human description (e.g. 'Austin loan-officer plan').",
                },
            },
            "required": ["file_path"],
        },
        fn=upload_to_cloud,
    )

    registry.register(
        name="list_cloud_files",
        description=(
            "List files in the user's Windy Cloud. Use when asked 'what's "
            "in my cloud?' or 'find the file I saved last week'. Optional "
            "prefix filters by name. Returns {status, files, count}."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prefix": {
                    "type": "string",
                    "description": "Optional name prefix filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max files to return (default 20, max 200).",
                },
            },
            "required": [],
        },
        fn=list_cloud_files,
    )
