"""Windy Code WEB tools — build the user's projects in the BROWSER builder.

The HTTPS sibling of ``windycode.py`` (which drives the desktop IDE over the
local Agent Bus socket). These tools target **windy-code-web** — the cloud
builder where grandma's projects live at windycode.ai — so the agent can build
for a user who never opens a desktop app: "make me a scrapbook" in chat →
project + files appear in her browser workspace, live preview fills in, and
publishing goes through an explicit confirm.

Design decisions:
  * **The builder is a thin client of Windy Cloud** — every file save becomes
    a Sites version with a HUMAN label (the user's Undo timeline reads
    "Added the beach photos", never "commit 3f2a"). Write labels for grandma.
  * **Annotation law**: every editable text node / image in generated HTML
    must carry ``data-windy-edit-id="<stable-key>"`` so click-to-edit works.
  * **Publish is an EXTERNAL EFFECT**: trust-gated here (ADR-019/020 pattern)
    AND the builder relays a confirm question — when a publish call returns
    ``confirm_required``, relay ``speak`` to the user VERBATIM, get their yes,
    then call publish again with the ``confirm_token``. Never invent consent.
  * **Never raises.** Unavailable/failed/denied come back as structured dicts
    the LLM can relay in plain words.

Environment:
    WINDY_CODE_WEB_URL       — builder API base, e.g. https://windycode.ai
                               (unset ⇒ tools report unavailable)
    ETERNITAS_PASSPORT_TOKEN / WINDY_JWT — the EPT presented as the bearer
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
_PUBLISH_TRUST_ACTION = "windycode_web_publish"


def _creds() -> tuple[str, str]:
    """Resolve (builder_url, token). Empty strings indicate not configured."""
    url = os.environ.get("WINDY_CODE_WEB_URL", "").rstrip("/")
    token = (
        os.environ.get("ETERNITAS_PASSPORT_TOKEN", "")
        or os.environ.get("WINDY_JWT", "")
    )
    return url, token


def _trust_gate_enabled() -> bool:
    """Trust gate runs only when the agent has a passport (hatch sets it)."""
    return bool(os.environ.get("ETERNITAS_PASSPORT", "").strip())


def _request(method: str, path: str, json_body: dict | None = None) -> dict[str, Any]:
    """One HTTP call to the builder; never raises."""
    base, token = _creds()
    if not base or not token:
        return {
            "status": "unavailable",
            "error": (
                "The browser builder is not configured for this agent. "
                "WINDY_CODE_WEB_URL and an Eternitas token "
                "(ETERNITAS_PASSPORT_TOKEN or WINDY_JWT) must be set."
            ),
        }
    try:
        resp = httpx.request(
            method,
            f"{base}/api/v1/projects{path}",
            json=json_body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
    except httpx.ConnectError as exc:
        return {"status": "failed", "error": f"Cannot reach the builder at {base}: {exc}"}
    except httpx.HTTPError as exc:
        return {"status": "failed", "error": f"Builder transport error: {exc}"}

    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code in (200, 201):
        return {"status": "ok", **body}
    detail = body.get("detail", body) if isinstance(body, dict) else {}
    if isinstance(detail, dict) and detail.get("speak"):
        # The builder speaks grandma — relay its own words + repair pointer.
        return {"status": "failed", "http_status": resp.status_code, **detail}
    return {
        "status": "failed",
        "http_status": resp.status_code,
        "error": f"Builder returned {resp.status_code}",
    }


# ─── tool implementations ────────────────────────────────────────────


def windycodeweb_status() -> dict[str, Any]:
    """Reachability probe: configured + the builder answers."""
    base, token = _creds()
    if not base or not token:
        return _request("GET", "")  # returns the structured unavailable dict
    try:
        resp = httpx.get(f"{base}/version", timeout=10.0)
        info = resp.json() if resp.status_code == 200 else {}
        return {"status": "connected", "builder": info}
    except httpx.HTTPError as exc:
        return {"status": "unavailable", "error": f"Builder unreachable: {exc}"}


def windycodeweb_list_projects() -> dict[str, Any]:
    return _request("GET", "")


def windycodeweb_create_project(name: str, kind: str = "site") -> dict[str, Any]:
    if not name or not name.strip():
        return {"status": "failed", "error": "Project name is empty"}
    return _request("POST", "", {"name": name.strip(), "kind": kind})


def windycodeweb_add_files(project_id: str, files: dict[str, str], label: str) -> dict[str, Any]:
    if not isinstance(files, dict) or not files:
        return {"status": "failed", "error": "files must be a non-empty {path: content} object"}
    if not label or not label.strip():
        return {
            "status": "failed",
            "error": (
                "label is required — a short human sentence like "
                "'Added the beach photos' (the user reads these as their Undo list)"
            ),
        }
    return _request(
        "POST", f"/{project_id}/checkpoints", {"files": files, "label": label.strip()}
    )


def windycodeweb_list_checkpoints(project_id: str) -> dict[str, Any]:
    return _request("GET", f"/{project_id}/checkpoints")


def windycodeweb_undo(project_id: str, checkpoint_id: str) -> dict[str, Any]:
    return _request("POST", f"/{project_id}/undo", {"checkpoint_id": checkpoint_id})


def windycodeweb_project_status(project_id: str) -> dict[str, Any]:
    return _request("GET", f"/{project_id}/status")


def windycodeweb_publish(project_id: str, confirm_token: str = "") -> dict[str, Any]:
    # Publish is an external effect: gate on the trust plane (ADR-019/020)
    # exactly like post_chat_message does, then let the builder's own
    # confirm-flow do the human-consent half.
    if _trust_gate_enabled():
        from windyfly.trust.gate import TrustDenied, require_trust

        try:
            asyncio.run(require_trust(_PUBLISH_TRUST_ACTION))
        except TrustDenied as denied:
            return {
                "status": "denied",
                "reason": denied.reason,
                "band": denied.band,
                "action": _PUBLISH_TRUST_ACTION,
                "error": str(denied),
            }
        except Exception as exc:  # fail-open with loud log, matching chat.py
            logger.warning("Trust gate check errored (fail-open): %s", exc)
    body: dict[str, Any] = {}
    if confirm_token.strip():
        body["confirm_token"] = confirm_token.strip()
    return _request("POST", f"/{project_id}/publish", body)


# ─── registration ────────────────────────────────────────────────────


def register_windycodeweb_tools(registry: ToolRegistry) -> None:
    """Register the browser-builder tools."""
    _pid = {
        "type": "string",
        "description": "The project id returned by windycodeweb_create_project / list.",
    }

    registry.register(
        name="windycodeweb_status",
        description=(
            "Check whether the browser builder (Windy Code on the web) is "
            "configured and reachable. Call this first if a windycodeweb_* "
            "tool returns 'unavailable'."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=windycodeweb_status,
    )

    registry.register(
        name="windycodeweb_list_projects",
        description=(
            "List the user's projects in the browser builder — the answer to "
            "'what am I building?' Each has {id, name, state, speak}."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=windycodeweb_list_projects,
    )

    registry.register(
        name="windycodeweb_create_project",
        description=(
            "Start a new project in the user's BROWSER builder (their private "
            "draft website) — use FIRST when they ask you to build something "
            "and they aren't at a desktop with Windy Code open. Returns "
            "{project:{id,...}, speak}."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Plain-words project name, e.g. 'Garden Club'."},
                "kind": {"type": "string",
                         "description": "site (default) | scrapbook | pamphlet"},
            },
            "required": ["name"],
        },
        fn=windycodeweb_create_project,
    )

    registry.register(
        name="windycodeweb_add_files",
        description=(
            "Save files into a builder project as ONE checkpoint the user can "
            "undo to. files = {path: full text content} (e.g. index.html). "
            "label = a short HUMAN sentence describing the change ('Added the "
            "beach photos') — the user reads these, never filenames. LAW: "
            "every editable text node/image in your HTML must carry "
            'data-windy-edit-id="<stable-key>" so click-to-edit works. '
            "Idempotent: identical content is not saved twice."
        ),
        parameters={
            "type": "object",
            "properties": {
                "project_id": _pid,
                "files": {
                    "type": "object",
                    "description": "{relative/path.html: full file content}",
                    "additionalProperties": {"type": "string"},
                },
                "label": {"type": "string",
                          "description": "Human sentence for the Undo timeline."},
            },
            "required": ["project_id", "files", "label"],
        },
        fn=windycodeweb_add_files,
    )

    registry.register(
        name="windycodeweb_list_checkpoints",
        description=(
            "The project's Undo timeline — every saved version with its human "
            "label, newest first. Use before windycodeweb_undo."
        ),
        parameters={"type": "object", "properties": {"project_id": _pid},
                    "required": ["project_id"]},
        fn=windycodeweb_list_checkpoints,
    )

    registry.register(
        name="windycodeweb_undo",
        description=(
            "The giant Undo: put an earlier saved version back as the working "
            "version. Reversible; does NOT change what's published."
        ),
        parameters={
            "type": "object",
            "properties": {
                "project_id": _pid,
                "checkpoint_id": {"type": "string",
                                  "description": "id from windycodeweb_list_checkpoints."},
            },
            "required": ["project_id", "checkpoint_id"],
        },
        fn=windycodeweb_undo,
    )

    registry.register(
        name="windycodeweb_project_status",
        description=(
            "Is the site live, and where? Returns {state, url, speak}. Use to "
            "answer 'is my site up?' and to read the user their link."
        ),
        parameters={"type": "object", "properties": {"project_id": _pid},
                    "required": ["project_id"]},
        fn=windycodeweb_project_status,
    )

    registry.register(
        name="windycodeweb_publish",
        description=(
            "Put the project online — an EXTERNAL EFFECT. If the result has "
            "confirm_required, relay its 'speak' question to the user "
            "VERBATIM, get their explicit yes, then call again with the "
            "confirm_token. Never publish without the user's yes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "project_id": _pid,
                "confirm_token": {
                    "type": "string",
                    "description": "From the confirm_required response, after the user says yes.",
                },
            },
            "required": ["project_id"],
        },
        fn=windycodeweb_publish,
    )
