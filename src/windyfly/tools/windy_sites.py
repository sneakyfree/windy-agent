"""Windy Cloud Sites tools — the agent's hands on the hosting cell.

Talks to windy-cloud-sites over HTTPS (cloud.windycloud.com/api/v1/sites,
or WINDY_SITES_URL for dev). Same conventions as windy_domains: env-gated,
EPT bearer, never-raise. Publishing/undo/connect are CONFIRM_FLOW: the tool
returns a confirm_required question to relay, then the same tool with the
confirm_token completes it.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0


def _base_url() -> str:
    return os.environ.get(
        "WINDY_SITES_URL", "https://cloud.windycloud.com/api/v1/sites"
    ).rstrip("/")


def _ept() -> str:
    return os.environ.get("ETERNITAS_PASSPORT_TOKEN", "").strip()


def is_configured() -> bool:
    return bool(_ept())


def _unavailable() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "message": "Windy Cloud Sites isn't connected for this helper yet "
        "(no Eternitas passport token). Tell the owner to finish setup.",
    }


def _request(method: str, path: str, **kw: Any) -> dict[str, Any]:
    if not is_configured():
        return _unavailable()
    try:
        r = httpx.request(
            method,
            f"{_base_url()}{path}",
            headers={"Authorization": f"Bearer {_ept()}"},
            timeout=_TIMEOUT,
            follow_redirects=True,
            **kw,
        )
    except httpx.HTTPError as e:
        logger.warning("windy-sites %s %s failed: %s", method, path, e)
        return {"error": "Windy Cloud Sites isn't reachable right now.", "detail": str(e)}
    if r.status_code >= 400:
        try:
            body = r.json()
        except Exception:
            body = {"error": r.text[:200]}
        detail = body.get("detail", body)
        return {"failed": True, "status_code": r.status_code, **(
            detail if isinstance(detail, dict) else {"error": str(detail)}
        )}
    return r.json()


def create_site(display_name: str) -> dict[str, Any]:
    """Start a new website (a private draft until published)."""
    return _request("POST", "", json={"display_name": display_name, "created_via": "agent"})


def add_or_edit_files(site_id: str, files: dict[str, str], label: str) -> dict[str, Any]:
    """Save a new version of a site. files = { path: base64_content }.
    label is a HUMAN sentence ('Added the beach photos') — the Undo timeline
    grandma reads. Idempotent by content."""
    return _request(
        "POST",
        f"/{site_id}/versions",
        json={"files": files, "note": label, "source": "agent"},
    )


def publish_site(site_id: str, version_id: str, confirm_token: str = "") -> dict[str, Any]:
    """Put a version live. Returns confirm_required first — relay the
    question, then call again with the confirm_token after a yes."""
    body: dict[str, Any] = {"version_id": version_id}
    if confirm_token:
        body["confirm_token"] = confirm_token
    return _request("POST", f"/{site_id}/publish", json=body)


def undo_to_version(site_id: str, version_id: str, confirm_token: str = "") -> dict[str, Any]:
    """The giant Undo — put an earlier version back live. Same confirm flow."""
    body: dict[str, Any] = {"version_id": version_id}
    if confirm_token:
        body["confirm_token"] = confirm_token
    return _request("POST", f"/{site_id}/rollback", json=body)


def connect_domain_to_site(
    site_id: str, fqdn: str, confirm_token: str = "", bundle_actions: list | None = None
) -> dict[str, Any]:
    """Put a purchased domain on a site. If this rides a buy_domain bundle,
    pass the SAME confirm_token plus bundle_actions — one yes covers both."""
    body: dict[str, Any] = {"fqdn": fqdn}
    if confirm_token:
        body["confirm_token"] = confirm_token
    if bundle_actions is not None:
        body["bundle_actions"] = bundle_actions
    return _request("POST", f"/{site_id}/connect-domain", json=body)


def list_my_sites() -> dict[str, Any]:
    """Everything this account is building. Good for 'read me my projects'."""
    return _request("GET", "")


def site_status(site_id: str) -> dict[str, Any]:
    """Is a site up, and where? Returns { state, url, speak }."""
    return _request("GET", f"/{site_id}/status")


def list_versions(site_id: str) -> dict[str, Any]:
    """The Undo timeline for a site."""
    return _request("GET", f"/{site_id}/versions")


def register_windy_sites_tools(registry: ToolRegistry) -> None:
    registry.register(
        name="create_site",
        description=(
            "Start a new website called X (a private draft until published). "
            "Returns { site: {id, slug, state}, speak }."
        ),
        parameters={
            "type": "object",
            "properties": {"display_name": {"type": "string"}},
            "required": ["display_name"],
        },
        fn=lambda display_name: create_site(display_name),
    )
    registry.register(
        name="add_or_edit_files",
        description=(
            "Save a new version of a site (a checkpoint on the Undo timeline). "
            "files is a map of path -> base64 content. label is a HUMAN "
            "sentence like 'Added the welcome page' — grandma reads this list. "
            "Idempotent: the same files return the same version."
        ),
        parameters={
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
                "files": {"type": "object", "description": "path -> base64 content"},
                "label": {"type": "string", "description": "A plain human sentence."},
            },
            "required": ["site_id", "files", "label"],
        },
        fn=lambda site_id, files, label: add_or_edit_files(site_id, files, label),
    )
    registry.register(
        name="publish_site",
        description=(
            "Put a version of a site live. ALWAYS returns confirm_required "
            "with a question — RELAY IT VERBATIM and call again with the "
            "confirm_token after the human says yes. Publishing is explicit; "
            "drafts never go live by accident."
        ),
        parameters={
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
                "version_id": {"type": "string"},
                "confirm_token": {"type": "string"},
            },
            "required": ["site_id", "version_id"],
        },
        fn=lambda site_id, version_id, confirm_token="": publish_site(
            site_id, version_id, confirm_token
        ),
    )
    registry.register(
        name="undo_to_version",
        description=(
            "The giant Undo: put an EARLIER version of a site back live. Same "
            "confirm flow as publish_site."
        ),
        parameters={
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
                "version_id": {"type": "string"},
                "confirm_token": {"type": "string"},
            },
            "required": ["site_id", "version_id"],
        },
        fn=lambda site_id, version_id, confirm_token="": undo_to_version(
            site_id, version_id, confirm_token
        ),
    )
    registry.register(
        name="connect_domain_to_site",
        description=(
            "Put a purchased domain on a site. If this rides a buy_domain "
            "bundle, pass the SAME confirm_token plus bundle_actions so the "
            "human's one yes covers both; otherwise you'll get a confirm "
            "question to relay."
        ),
        parameters={
            "type": "object",
            "properties": {
                "site_id": {"type": "string"},
                "fqdn": {"type": "string"},
                "confirm_token": {"type": "string"},
                "bundle_actions": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["site_id", "fqdn"],
        },
        fn=lambda site_id, fqdn, confirm_token="", bundle_actions=None: connect_domain_to_site(
            site_id, fqdn, confirm_token, bundle_actions
        ),
    )
    registry.register(
        name="list_my_sites",
        description=(
            "List everything this account is building. Use for 'read me my "
            "projects' / 'what am I building?'. Returns { sites, speak }."
        ),
        parameters={"type": "object", "properties": {}},
        fn=lambda: list_my_sites(),
    )
    registry.register(
        name="site_status",
        description="Is a site up, and where? Returns { state, url, speak }.",
        parameters={
            "type": "object",
            "properties": {"site_id": {"type": "string"}},
            "required": ["site_id"],
        },
        fn=lambda site_id: site_status(site_id),
    )
    registry.register(
        name="list_site_versions",
        description="The Undo timeline for a site — every saved version.",
        parameters={
            "type": "object",
            "properties": {"site_id": {"type": "string"}},
            "required": ["site_id"],
        },
        fn=lambda site_id: list_versions(site_id),
    )
