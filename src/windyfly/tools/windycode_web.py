"""Windy Code WEB tools — build the user's projects in the BROWSER builder.

The HTTPS sibling of ``windycode.py`` (which drives the desktop IDE over the
local Agent Bus socket). These tools target **windy-code-web** — the cloud
builder where grandma's projects live at windycode.org — so the agent can build
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
    ``confirm_required``, relay its question VERBATIM; the token is held in code
    and only the OWNER's own reply ("yes, publish") spends it. Never invent consent.
  * **Never raises.** Unavailable/failed/denied come back as structured dicts
    the LLM can relay in plain words.

Environment:
    WINDY_CODE_WEB_URL       — builder API base (the live builder is
                               https://cloud.windycloud.com; windycode.org is only
                               the marketing site). "off" disables these tools.
    WINDY_CODE_WEB_DEFAULT   — "1" = with no WINDY_CODE_WEB_URL, use the live
                               builder AND tell the model to build sites there.
                               DARK (off) until merge-mode saves are live in the
                               builder: today a save replaces the whole site, so a
                               one-file save would delete the other files.
    ETERNITAS_PASSPORT_TOKEN / WINDY_JWT — the EPT presented as the bearer
"""

from __future__ import annotations

import asyncio
import logging
import time
import os
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
# The live builder (portal at /build/, API at /api/v1/projects). Hatch never set
# WINDY_CODE_WEB_URL, so these tools always answered "unavailable" and agents
# built sites around Windy Code. Used as the default only when
# WINDY_CODE_WEB_DEFAULT=1 (Hub flips it once merge-mode saves are live).
DEFAULT_BUILDER_URL = "https://cloud.windycloud.com"


def builder_default_enabled() -> bool:
    """WINDY_CODE_WEB_DEFAULT=1: the live builder is the default site path."""
    return os.environ.get("WINDY_CODE_WEB_DEFAULT", "").strip() == "1"


_PUBLISH_TRUST_ACTION = "windycode_web_publish"


def _creds() -> tuple[str, str]:
    """Resolve (builder_url, token). Empty strings indicate not configured."""
    url = os.environ.get("WINDY_CODE_WEB_URL", "").strip()
    if not url and builder_default_enabled():
        url = DEFAULT_BUILDER_URL
    url = url.rstrip("/")
    if url.lower() == "off":
        url = ""
    token = (
        os.environ.get("ETERNITAS_PASSPORT_TOKEN", "")
        or os.environ.get("WINDY_JWT", "")
    )
    return url, token


def _trust_gate_enabled() -> bool:
    """Trust gate runs only when the agent has a passport (hatch sets it)."""
    return bool(os.environ.get("ETERNITAS_PASSPORT", "").strip())


# The ONE agent contract (windy-code-web contracts/AGENT_BUILDER_CONTRACT.v1.md):
# every call is an MCP tools/call on the builder's own surface, the same one the
# Windy Chat roster uses. A vendored copy of the manifest
# (contracts/windy-code-web.mcp.v1.json) is drift-tested against _CONTRACT.
_MCP_PATH = "/api/v1/projects/mcp"

# Fly tool -> (builder MCP tool, the argument names this client may send).
_CONTRACT: dict[str, tuple[str, frozenset[str]]] = {
    "windycodeweb_list_projects": ("list_projects", frozenset()),
    "windycodeweb_list_templates": ("list_templates", frozenset()),
    "windycodeweb_start": ("start_from_prompt", frozenset({"prompt", "fills"})),
    "windycodeweb_start_from_template": ("start_from_template", frozenset({"slug", "name", "fills"})),
    "windycodeweb_create_project": ("create_project", frozenset({"name", "kind"})),
    "windycodeweb_add_files": ("add_or_edit_files",
                               frozenset({"project_id", "files", "label", "mode", "delete"})),
    "windycodeweb_list_editables": ("list_editables", frozenset({"project_id"})),
    "windycodeweb_edit_text": ("edit_text", frozenset({"project_id", "edit_id", "new_value"})),
    "windycodeweb_list_checkpoints": ("list_checkpoints", frozenset({"project_id"})),
    "windycodeweb_undo": ("undo_to_checkpoint", frozenset({"project_id", "checkpoint_id"})),
    "windycodeweb_project_status": ("project_status", frozenset({"project_id"})),
    "windycodeweb_preview": ("preview_project", frozenset({"project_id"})),
    "windycodeweb_publish": ("publish_project", frozenset({"project_id", "confirm_token"})),
    "windycodeweb_unpublish": ("unpublish_project", frozenset({"project_id", "confirm_token"})),
    "windycodeweb_connect_domain": ("connect_domain",
                                    frozenset({"project_id", "fqdn", "confirm_token",
                                               "bundle_actions"})),
}


def _unavailable() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "error": (
            "The browser builder is not configured for this agent. "
            "WINDY_CODE_WEB_URL (or WINDY_CODE_WEB_DEFAULT=1) and an "
            "Eternitas token (ETERNITAS_PASSPORT_TOKEN or WINDY_JWT) "
            "must be set."
        ),
    }


def _rpc(base: str, token: str, method: str, params: dict[str, Any]) -> httpx.Response:
    return httpx.post(
        f"{base}{_MCP_PATH}",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT,
    )


def _call(fly_tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """One builder MCP tools/call; never raises. Unknown args are a bug here."""
    tool, allowed = _CONTRACT[fly_tool]
    extra = set(args) - allowed
    if extra:  # caught by the drift test; never sent
        return {"status": "failed", "error": f"internal: {fly_tool} sent {sorted(extra)}"}
    base, token = _creds()
    if not base or not token:
        return _unavailable()
    try:
        resp = _rpc(base, token, "tools/call", {"name": tool, "arguments": args})
    except httpx.ConnectError as exc:
        return {"status": "failed", "error": f"Cannot reach the builder at {base}: {exc}"}
    except httpx.HTTPError as exc:
        return {"status": "failed", "error": f"Builder transport error: {exc}"}
    try:
        frame = resp.json()
    except ValueError:
        frame = {}
    result = frame.get("result") if isinstance(frame, dict) else None
    if resp.status_code != 200 or not isinstance(result, dict):
        err = (frame.get("error") or {}).get("message", "") if isinstance(frame, dict) else ""
        return {"status": "failed", "http_status": resp.status_code,
                "error": err or f"Builder returned {resp.status_code}"}
    body = result.get("structuredContent") or {}
    if result.get("isError") or body.get("failed"):
        out: dict[str, Any] = {"status": "failed", **{k: v for k, v in body.items() if k != "failed"}}
        if body.get("code") == "auth_required":
            out["status"] = "unavailable"
        elif body.get("code") == "agent_daily_limit":
            # The builder's own plain words; this agent's budget is spent for today.
            out["retry"] = "tomorrow"
        return out
    return {"status": "ok", **body}


# ─── tool implementations ────────────────────────────────────────────


def windycodeweb_status() -> dict[str, Any]:
    """Reachability probe: configured + the builder answers + contract check."""
    base, token = _creds()
    if not base or not token:
        return _unavailable()
    try:
        resp = _rpc(base, token, "tools/list", {})
        tools = {t.get("name") for t in resp.json().get("result", {}).get("tools", [])}
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        return {"status": "unavailable", "error": f"Builder unreachable: {exc}"}
    missing = sorted({t for t, _ in _CONTRACT.values()} - tools)
    return {"status": "connected" if not missing else "degraded",
            "builder_tools": len(tools), "missing_tools": missing}


def windycodeweb_list_projects() -> dict[str, Any]:
    return _call("windycodeweb_list_projects", {})


def windycodeweb_list_templates() -> dict[str, Any]:
    return _call("windycodeweb_list_templates", {})


def _clean_fills(fills: Any) -> dict[str, str]:
    if not isinstance(fills, dict):
        return {}
    return {str(k): str(v) for k, v in fills.items() if str(v).strip()}


def windycodeweb_start(prompt: str, fills: dict[str, str] | None = None) -> dict[str, Any]:
    if not prompt or not prompt.strip():
        return {"status": "failed", "error": "prompt is empty: say what to build in a few words"}
    args: dict[str, Any] = {"prompt": prompt.strip()[:200]}
    if _clean_fills(fills):
        args["fills"] = _clean_fills(fills)
    return _call("windycodeweb_start", args)


def windycodeweb_start_from_template(
    slug: str, name: str = "", fills: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not slug or not slug.strip():
        return {"status": "failed", "error": "slug is required (from windycodeweb_list_templates)"}
    args: dict[str, Any] = {"slug": slug.strip(), "name": (name or "").strip()}
    if _clean_fills(fills):
        args["fills"] = _clean_fills(fills)
    return _call("windycodeweb_start_from_template", args)


def windycodeweb_create_project(name: str, kind: str = "site") -> dict[str, Any]:
    if not name or not name.strip():
        return {"status": "failed", "error": "Project name is empty"}
    return _call("windycodeweb_create_project", {"name": name.strip(), "kind": kind})


def windycodeweb_add_files(
    project_id: str,
    files: dict[str, str] | None = None,
    label: str = "",
    binary_files: dict[str, str] | None = None,
    delete: list[str] | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    import base64 as _b64
    import binascii

    files = files or {}
    binary_files = binary_files or {}
    delete = [str(p) for p in (delete or [])]
    if not isinstance(files, dict) or not isinstance(binary_files, dict):
        return {"status": "failed", "error": "files and binary_files must be {path: content} objects"}
    if not files and not binary_files and not delete:
        return {"status": "failed", "error": "nothing to save: give files, binary_files or delete"}
    if not label or not label.strip():
        return {
            "status": "failed",
            "error": (
                "label is required — a short human sentence like "
                "'Added the beach photos' (the user reads these as their Undo list)"
            ),
        }
    # The wire is base64 (binary-safe, matches Windy Cloud Sites). Text files
    # are encoded here so the model never has to; binary files (images) arrive
    # already base64 and are checked, never re-encoded.
    wire = {str(p): _b64.b64encode(str(c).encode()).decode() for p, c in files.items()}
    for path, b64 in binary_files.items():
        try:
            _b64.b64decode(str(b64), validate=True)
        except (binascii.Error, ValueError):
            return {"status": "failed", "error": f"binary_files[{path!r}] is not valid base64"}
        wire[str(path)] = str(b64)
    args: dict[str, Any] = {"project_id": project_id, "files": wire, "label": label.strip(),
                            "mode": "replace" if replace else "merge"}
    if delete:
        args["delete"] = delete
    return _call("windycodeweb_add_files", args)


def windycodeweb_list_editables(project_id: str) -> dict[str, Any]:
    return _call("windycodeweb_list_editables", {"project_id": project_id})


def windycodeweb_edit_text(project_id: str, edit_id: str, new_value: str) -> dict[str, Any]:
    if not edit_id or not str(new_value).strip():
        return {"status": "failed", "error": "edit_id and new_value are required"}
    return _call("windycodeweb_edit_text",
                 {"project_id": project_id, "edit_id": edit_id, "new_value": str(new_value)})


def windycodeweb_list_checkpoints(project_id: str) -> dict[str, Any]:
    return _call("windycodeweb_list_checkpoints", {"project_id": project_id})


def windycodeweb_undo(project_id: str, checkpoint_id: str) -> dict[str, Any]:
    return _call("windycodeweb_undo", {"project_id": project_id, "checkpoint_id": checkpoint_id})


def windycodeweb_project_status(project_id: str) -> dict[str, Any]:
    return _call("windycodeweb_project_status", {"project_id": project_id})


def windycodeweb_preview(project_id: str) -> dict[str, Any]:
    return _call("windycodeweb_preview", {"project_id": project_id})


def _publish_gate() -> dict[str, Any] | None:
    """Trust plane first (ADR-019/020). FAILS CLOSED: a trust check that errors refuses
    (Hub, 10-01: nothing goes live on an unverifiable agent). None = proceed."""
    if not _trust_gate_enabled():
        return None
    from windyfly.trust.gate import TrustDenied, require_trust

    try:
        asyncio.run(require_trust(_PUBLISH_TRUST_ACTION))
    except TrustDenied as denied:
        return {"status": "denied", "reason": denied.reason, "band": denied.band,
                "action": _PUBLISH_TRUST_ACTION, "error": str(denied)}
    except Exception as exc:
        logger.warning("Trust gate check errored (fail-closed): %s", exc)
        return {"status": "denied", "reason": "trust_check_unavailable",
                "action": _PUBLISH_TRUST_ACTION,
                "error": "I couldn't verify my standing right now, so I didn't do it. Try again shortly."}
    return None


# ── owner-held confirmation (Hub, 10-01) ─────────────────────────────
# The builder answers an agent's publish / unpublish / connect_domain with
# confirm_required + a confirm_token. That token is HELD HERE, never shown to the
# model; only the OWNER's own message ("yes, publish" / "yes, unpublish" /
# "yes, connect"), intercepted in code by channels.base, spends it. A token the
# model passes back in is refused.
_HELD: dict[str, dict[str, Any]] = {}
HOLD_TTL_S = 30 * 60
_WORD = {"windycodeweb_publish": "publish", "windycodeweb_unpublish": "unpublish",
         "windycodeweb_connect_domain": "connect"}


def _refuse_model_token() -> dict[str, Any]:
    return {"status": "refused", "done": False,
            "error": ("Only the owner can confirm this, by replying in chat. "
                      "Call the tool without a confirm_token and relay its question.")}


def _gated(fly_tool: str, args: dict[str, Any], model_token: str) -> dict[str, Any]:
    if model_token.strip():
        return _refuse_model_token()
    denied = _publish_gate()
    if denied:
        return denied
    out = _call(fly_tool, args)
    if out.get("status") == "ok" and out.get("confirm_required") and out.get("confirm_token"):
        word = _WORD[fly_tool]
        _HELD[word] = {"fly_tool": fly_tool, "args": dict(args),
                       "token": str(out["confirm_token"]), "at": time.time()}
        question = str(out.get("speak") or "Do it?")
        return {"status": "confirm_required", "done": False,
                "question": f"{question} (Reply \"yes, {word}\" to go ahead.)",
                "note": ("NOT DONE. Relay the question to the owner verbatim. Only "
                         "their reply confirms it; do not call this tool again.")}
    out.pop("confirm_token", None)  # a token is never handed to the model
    if out.get("status") == "ok" and out.get("owner_confirm") == "sent":
        # Builder-side approval (Windy Inbox): the owner was asked there directly.
        # Contract v1.1: approval_id / expires_at / already_asked; the later outcome
        # shows up in project_status.owner_confirms.
        return {"status": "awaiting_owner", "done": False,
                "speak": out.get("speak") or "I've asked you to approve it in your Windy Inbox.",
                "approval_id": out.get("approval_id"), "expires_at": out.get("expires_at"),
                "already_asked": bool(out.get("already_asked")),
                "note": ("NOT DONE yet. Relay 'speak' to the owner; do not call this tool again. "
                         "Check project_status later for the outcome.")}
    return out


def owner_confirm(text: str) -> str | None:
    """The OWNER's own reply ("yes, publish" etc.), handled in code by channels.base.
    None = not a confirmation for anything held here."""
    words = [w for w in text.strip().lower().replace(",", " ").replace(".", " ").split() if w]
    if not words or words[-1] not in ("publish", "unpublish", "connect"):
        return None
    if len(words) > 2 or (len(words) == 2 and words[0] != "yes"):
        return None
    word = words[-1]
    held = _HELD.pop(word, None)
    if held is None:
        return None
    if time.time() - held["at"] > HOLD_TTL_S:
        return f"That {word} request expired. Ask me again."
    denied = _publish_gate()
    if denied:
        return f"Not done: {denied['error']}"
    out = _call(held["fly_tool"], {**held["args"], "confirm_token": held["token"]})
    if out.get("status") == "ok":
        return str(out.get("speak") or f"Done: {word}.")
    return f"Not done: {out.get('error') or out.get('status')}"


def windycodeweb_unpublish(project_id: str, confirm_token: str = "") -> dict[str, Any]:
    return _gated("windycodeweb_unpublish", {"project_id": project_id}, confirm_token)


def windycodeweb_connect_domain(
    project_id: str,
    fqdn: str,
    confirm_token: str = "",
    bundle_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not fqdn or "." not in fqdn:
        return {"status": "failed",
                "error": "fqdn must be a full name like grandmarose.com"}
    args: dict[str, Any] = {"project_id": project_id, "fqdn": fqdn.strip().lower()}
    if bundle_actions:
        args["bundle_actions"] = bundle_actions
    return _gated("windycodeweb_connect_domain", args, confirm_token)


def windycodeweb_publish(project_id: str, confirm_token: str = "") -> dict[str, Any]:
    return _gated("windycodeweb_publish", {"project_id": project_id}, confirm_token)


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

    _fills = {
        "type": "object",
        "additionalProperties": {"type": "string"},
        "description": ("The person's own first words, {edit_id: text}, e.g. "
                        "{\"headline\": \"Rosa's Bakery\", \"intro\": \"Fresh bread daily\"}. "
                        "They appear in the FIRST preview."),
    }

    registry.register(
        name="windycodeweb_start",
        description=(
            "FASTEST way to start a site for the user: give what they asked "
            "for in plain words and your first words (fills). Windy Code "
            "picks the closest finished example and puts the words in, so the "
            "first preview already looks like theirs. Then use "
            "windycodeweb_edit_text / windycodeweb_add_files to change it. "
            "Returns {project, template, filled, editables, speak}."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string",
                           "description": "What they asked for, e.g. 'a page for my bakery'."},
                "fills": _fills,
            },
            "required": ["prompt"],
        },
        fn=windycodeweb_start,
    )

    registry.register(
        name="windycodeweb_list_templates",
        description="The finished examples a project can start from (slug, title, what it suits).",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=windycodeweb_list_templates,
    )

    registry.register(
        name="windycodeweb_start_from_template",
        description=(
            "Start a project from a specific example (slug from "
            "windycodeweb_list_templates), with optional first words (fills). "
            "Returns the project and a short interview: ask its questions ONE "
            "AT A TIME, then put the answers in with windycodeweb_edit_text."
        ),
        parameters={
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "name": {"type": "string", "description": "Their name for it."},
                "fills": _fills,
            },
            "required": ["slug"],
        },
        fn=windycodeweb_start_from_template,
    )

    registry.register(
        name="windycodeweb_list_editables",
        description=(
            "The words and pictures on the project that can be changed "
            "(edit_id, current value). Use before windycodeweb_edit_text."
        ),
        parameters={"type": "object", "properties": {"project_id": _pid},
                    "required": ["project_id"]},
        fn=windycodeweb_list_editables,
    )

    registry.register(
        name="windycodeweb_edit_text",
        description=(
            "Change ONE piece of text (or an image address) on the project by "
            "its edit_id. Saves a new Undo point automatically. Best for "
            "'change the headline to …'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "project_id": _pid,
                "edit_id": {"type": "string", "description": "From windycodeweb_list_editables."},
                "new_value": {"type": "string"},
            },
            "required": ["project_id", "edit_id", "new_value"],
        },
        fn=windycodeweb_edit_text,
    )

    registry.register(
        name="windycodeweb_create_project",
        description=(
            "Start a new project in Windy Code, the user's BROWSER builder "
            "(their private draft website). This is the DEFAULT way to build "
            "a website or web page for the user: use it instead of "
            "create_site, writing files, or shell commands, unless they are "
            "at a desktop with Windy Code open (then use windycode_*). The "
            "user sees the project, its preview and its Undo list at "
            "cloud.windycloud.com/build/. Returns {project:{id,...}, speak}."
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
            "undo to. Send ONLY the files you changed: they are laid over the "
            "current version and every other file is kept. files = {path: full "
            "text content}; binary_files = {path: base64} for images; delete = "
            "paths to remove; replace=true only to start the whole site over. "
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
                    "description": "{relative/path.html: full file content} (changed files only)",
                    "additionalProperties": {"type": "string"},
                },
                "label": {"type": "string",
                          "description": "Human sentence for the Undo timeline."},
                "binary_files": {
                    "type": "object",
                    "description": "{images/photo.png: base64 bytes}",
                    "additionalProperties": {"type": "string"},
                },
                "delete": {"type": "array", "items": {"type": "string"},
                           "description": "Paths to remove from the site."},
                "replace": {"type": "boolean",
                            "description": "true = these files ARE the whole site (rare)."},
            },
            "required": ["project_id", "label"],
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
        name="windycodeweb_preview",
        description=(
            "A private 15-minute preview link of the project's WORKING "
            "version. Use to show the user how it looks BEFORE publishing "
            "('here's a private look: <preview_url>')."
        ),
        parameters={"type": "object", "properties": {"project_id": _pid},
                    "required": ["project_id"]},
        fn=windycodeweb_preview,
    )

    registry.register(
        name="windycodeweb_unpublish",
        description=(
            "Make a live site PRIVATE again (visitors stop seeing it; nothing "
            "is deleted). EXTERNAL EFFECT: returns confirm_required with a "
            "question; relay it VERBATIM. The owner confirms by replying; do "
            "not call again."
        ),
        parameters={
            "type": "object",
            "properties": {"project_id": _pid},
            "required": ["project_id"],
        },
        fn=windycodeweb_unpublish,
    )

    registry.register(
        name="windycodeweb_connect_domain",
        description=(
            "Put the user's project on their OWN domain ('put it on "
            "grandmarose.com'). EXTERNAL EFFECT: returns confirm_required "
            "with a question; relay it VERBATIM. The owner confirms by "
            "replying; do not call again. With a Domains bundle, pass "
            "bundle_actions verbatim."
        ),
        parameters={
            "type": "object",
            "properties": {
                "project_id": _pid,
                "fqdn": {"type": "string",
                         "description": "Full name, e.g. grandmarose.com."},
                "bundle_actions": {"type": "array", "items": {"type": "object"},
                                   "description": "EXACT actions list from the Domains bundle."},
            },
            "required": ["project_id", "fqdn"],
        },
        fn=windycodeweb_connect_domain,
    )

    registry.register(
        name="windycodeweb_publish",
        description=(
            "Put the project online — an EXTERNAL EFFECT. Returns "
            "confirm_required with a question; relay it to the owner VERBATIM. "
            "The owner confirms by replying; do not call again."
        ),
        parameters={
            "type": "object",
            "properties": {
                "project_id": _pid,
            },
            "required": ["project_id"],
        },
        fn=windycodeweb_publish,
    )
