"""Cloudflare API capabilities — read-only zone + DNS introspection.

Read-only first cut of the Cloudflare API surface so the bot can
answer normal questions about the operator's zones:

  - ``cloudflare.list_zones`` — every zone on the account
  - ``cloudflare.zone_details(zone)`` — status, plan, name servers
  - ``cloudflare.list_dns_records(zone, type=, name=)`` — DNS records

Read-only because Cloudflare DNS edits cross a high-blast-radius
trust line (one bad A record can take a domain down). Write capabs
land in a separate PR with ``Tier.EXTERNAL_EFFECT`` + dry_run +
TRUSTED+ band.

Auth contract
-------------

Token is read from ``CLOUDFLARE_API_TOKEN`` env var (Cloudflare's
modern Bearer-token auth — preferred over the legacy ``X-Auth-Key``
+ ``X-Auth-Email`` pair). Required scopes for read-only:

  - Zone:Read (account level)
  - DNS:Read

Without a token the capability is registered but every call returns a
graceful ``{ok: false, error: "Cloudflare not configured..."}`` —
mirrors the github + email pattern. Bot can self-explain instead of
crashing or silently failing.

Zone lookup by name
-------------------

Cloudflare's API requires a zone *id* for most endpoints, but humans
think in zone *names* ("windycloud.com"). Each handler accepts either
``zone_name`` or ``zone_id``; if only name is given we resolve via
``GET /zones?name=...`` first. One extra round-trip per call, but the
LLM never has to remember 18 different 32-char zone hashes.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.cloudflare.com/client/v4"
_DEFAULT_TIMEOUT_S = 15.0

# Hard ceiling on records returned per call. Cloudflare's per_page max
# is 50000 but a 50000-record dump would obliterate the LLM context.
_DEFAULT_PAGE_SIZE = 100
_MAX_PAGE_SIZE = 500


def _build_client(token: str | None) -> httpx.Client:
    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "windyfly-agent/0.5",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(
        base_url=_BASE_URL,
        headers=headers,
        timeout=_DEFAULT_TIMEOUT_S,
        follow_redirects=True,
    )


def _not_configured_error() -> dict[str, Any]:
    from windyfly.agent.setup_status import dormant_nudge
    return {
        "ok": False,
        "kind": "dormant_integration",
        "integration": "cloudflare",
        "error": dormant_nudge("cloudflare"),
    }


def _resolve_zone_id(
    *, zone_name: str | None, zone_id: str | None,
    client: httpx.Client,
) -> tuple[str | None, dict[str, Any] | None]:
    """Return (zone_id, None) on success or (None, error_dict) on failure.

    If ``zone_id`` is already provided, returns it as-is. Otherwise
    looks up by name via ``GET /zones?name=...`` and returns the first
    match's id.
    """
    if zone_id:
        return zone_id, None
    if not zone_name:
        return None, {
            "ok": False,
            "error": "either zone_id or zone_name is required",
        }
    try:
        resp = client.get("/zones", params={"name": zone_name})
    except httpx.HTTPError as e:
        return None, {
            "ok": False,
            "error": f"network error resolving zone {zone_name!r}: {e}",
        }
    if resp.status_code == 401 or resp.status_code == 403:
        return None, {
            "ok": False,
            "error": (
                f"unauthorized ({resp.status_code}) — token rejected. "
                "Check CLOUDFLARE_API_TOKEN has Zone:Read scope."
            ),
        }
    if resp.status_code >= 400:
        return None, {
            "ok": False,
            "error": f"cloudflare api {resp.status_code}: {resp.text[:200]}",
        }
    data = resp.json()
    results = data.get("result") or []
    if not results:
        return None, {
            "ok": False,
            "error": (
                f"no zone named {zone_name!r} found on this account. "
                "Use cloudflare.list_zones to see what's available."
            ),
        }
    return results[0].get("id"), None


def _list_zones_handler(
    *, token: str | None,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    if not token:
        return _not_configured_error()
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
    try:
        with _build_client(token) as client:
            resp = client.get("/zones", params={"per_page": page_size})
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"network error listing zones: {e}"}
    if resp.status_code in (401, 403):
        return {
            "ok": False,
            "error": (
                f"unauthorized ({resp.status_code}) — token rejected. "
                "Check CLOUDFLARE_API_TOKEN has Zone:Read scope."
            ),
        }
    if resp.status_code >= 400:
        return {"ok": False, "error": f"cloudflare api {resp.status_code}: {resp.text[:200]}"}
    data = resp.json()
    results = data.get("result") or []
    zones = [
        {
            "id": z.get("id"),
            "name": z.get("name"),
            "status": z.get("status"),
            "paused": z.get("paused"),
            "type": z.get("type"),
            "plan": (z.get("plan") or {}).get("name"),
            "name_servers": z.get("name_servers"),
        }
        for z in results
    ]
    info = data.get("result_info") or {}
    return {
        "ok": True,
        "count": len(zones),
        "total_count": info.get("total_count"),
        "zones": zones,
    }


def _zone_details_handler(
    *, zone_name: str | None, zone_id: str | None,
    token: str | None,
) -> dict[str, Any]:
    if not token:
        return _not_configured_error()
    with _build_client(token) as client:
        zid, err = _resolve_zone_id(
            zone_name=zone_name, zone_id=zone_id, client=client,
        )
        if err is not None:
            return err
        try:
            resp = client.get(f"/zones/{zid}")
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"network error: {e}"}
    if resp.status_code in (401, 403):
        return {
            "ok": False,
            "error": f"unauthorized ({resp.status_code}) for zone {zid}",
        }
    if resp.status_code == 404:
        return {"ok": False, "error": f"zone {zid!r} not found"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"cloudflare api {resp.status_code}: {resp.text[:200]}"}
    z = resp.json().get("result") or {}
    return {
        "ok": True,
        "id": z.get("id"),
        "name": z.get("name"),
        "status": z.get("status"),
        "paused": z.get("paused"),
        "type": z.get("type"),
        "development_mode": z.get("development_mode"),
        "name_servers": z.get("name_servers"),
        "original_name_servers": z.get("original_name_servers"),
        "plan": (z.get("plan") or {}).get("name"),
        "created_on": z.get("created_on"),
        "modified_on": z.get("modified_on"),
        "activated_on": z.get("activated_on"),
    }


def _list_dns_records_handler(
    *, zone_name: str | None, zone_id: str | None,
    type: str | None = None,
    name: str | None = None,
    token: str | None,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    if not token:
        return _not_configured_error()
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
    with _build_client(token) as client:
        zid, err = _resolve_zone_id(
            zone_name=zone_name, zone_id=zone_id, client=client,
        )
        if err is not None:
            return err
        params: dict[str, Any] = {"per_page": page_size}
        if type:
            params["type"] = type.upper()
        if name:
            params["name"] = name
        try:
            resp = client.get(f"/zones/{zid}/dns_records", params=params)
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"network error: {e}"}
    if resp.status_code in (401, 403):
        return {
            "ok": False,
            "error": (
                f"unauthorized ({resp.status_code}) — token may lack "
                "DNS:Read scope for this zone."
            ),
        }
    if resp.status_code == 404:
        return {"ok": False, "error": f"zone {zid!r} not found"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"cloudflare api {resp.status_code}: {resp.text[:200]}"}
    data = resp.json()
    records = [
        {
            "id": r.get("id"),
            "type": r.get("type"),
            "name": r.get("name"),
            "content": r.get("content"),
            "ttl": r.get("ttl"),
            "proxied": r.get("proxied"),
            "comment": r.get("comment"),
        }
        for r in (data.get("result") or [])
    ]
    info = data.get("result_info") or {}
    return {
        "ok": True,
        "zone_id": zid,
        "count": len(records),
        "total_count": info.get("total_count"),
        "records": records,
    }


def _find_existing_record(
    *, zone_id: str, type_: str, name: str,
    client: httpx.Client,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Find a DNS record on a zone matching exact (type, name).

    Returns (record, None) on match, (None, error_dict) on >1 match
    (we refuse to guess; the caller must disambiguate by record_id),
    or (None, None) when no record exists yet (set_dns_record will
    create instead of update).
    """
    try:
        resp = client.get(
            f"/zones/{zone_id}/dns_records",
            params={"type": type_.upper(), "name": name},
        )
    except httpx.HTTPError as e:
        return None, {"ok": False, "error": f"network error during lookup: {e}"}
    if resp.status_code in (401, 403):
        return None, {
            "ok": False,
            "error": (
                f"unauthorized ({resp.status_code}) — token may lack "
                "DNS:Read scope (which is needed even for write to "
                "look up the existing record id)."
            ),
        }
    if resp.status_code >= 400:
        return None, {
            "ok": False,
            "error": f"cloudflare api {resp.status_code}: {resp.text[:200]}",
        }
    results = (resp.json() or {}).get("result") or []
    if len(results) == 0:
        return None, None
    if len(results) > 1:
        return None, {
            "ok": False,
            "error": (
                f"{len(results)} records matched type={type_.upper()} "
                f"name={name!r}. Refusing to guess. List the records "
                "and pass record_id explicitly to disambiguate."
            ),
        }
    return results[0], None


def _set_dns_record_handler(
    *,
    zone_name: str | None, zone_id: str | None,
    type: str, name: str, content: str,
    ttl: int = 1,           # 1 = "auto" in Cloudflare's API
    proxied: bool | None = None,
    comment: str | None = None,
    dry_run: bool = False,
    token: str | None,
) -> dict[str, Any]:
    """Upsert a DNS record by (zone, type, name).

    If a record already exists for the given (type, name), PATCH it
    with the new content/ttl/proxied/comment. Otherwise POST a new
    record. Idempotent.

    Use ``dry_run=true`` first whenever a DNS edit could affect
    production traffic. ``proxied=None`` means "leave alone on update,
    use Cloudflare default on create".
    """
    if not token:
        return _not_configured_error()
    if not type or not name or not content:
        return {"ok": False, "error": "type, name, and content are required"}
    type_upper = type.upper()
    # Cloudflare-supported public types we routinely care about. Reject
    # obvious typos (TYPO/TYPE/SPF) at the door rather than 422-ing.
    _KNOWN_TYPES = {"A", "AAAA", "CNAME", "MX", "TXT", "NS", "SRV", "CAA", "PTR"}
    if type_upper not in _KNOWN_TYPES:
        return {
            "ok": False,
            "error": (
                f"type {type_upper!r} not in supported set "
                f"{sorted(_KNOWN_TYPES)}. Pass one of those."
            ),
        }

    with _build_client(token) as client:
        zid, err = _resolve_zone_id(
            zone_name=zone_name, zone_id=zone_id, client=client,
        )
        if err is not None:
            return err

        existing, lookup_err = _find_existing_record(
            zone_id=zid, type_=type_upper, name=name, client=client,
        )
        if lookup_err is not None:
            return lookup_err

        action = "update" if existing else "create"
        plan: dict[str, Any] = {
            "action": action,
            "zone_id": zid,
            "zone_name": zone_name,
            "type": type_upper,
            "name": name,
            "content": content,
            "ttl": ttl,
            "proxied": proxied,
            "comment": comment,
        }
        if existing:
            plan["existing_record_id"] = existing.get("id")
            plan["existing_content"] = existing.get("content")
            plan["existing_ttl"] = existing.get("ttl")
            plan["existing_proxied"] = existing.get("proxied")

        if dry_run:
            return {"plan": plan, "executed": False, "preview_only": True}

        body: dict[str, Any] = {
            "type": type_upper, "name": name, "content": content, "ttl": ttl,
        }
        if proxied is not None:
            body["proxied"] = proxied
        if comment is not None:
            body["comment"] = comment

        try:
            if existing:
                rec_id = existing.get("id")
                resp = client.patch(
                    f"/zones/{zid}/dns_records/{rec_id}", json=body,
                )
            else:
                resp = client.post(
                    f"/zones/{zid}/dns_records", json=body,
                )
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"network error during {action}: {e}"}

    if resp.status_code in (401, 403):
        return {
            "ok": False,
            "error": (
                f"unauthorized ({resp.status_code}) — token may lack "
                "DNS:Edit scope on this zone."
            ),
        }
    if resp.status_code == 422:
        return {
            "ok": False,
            "error": f"validation error (422): {resp.text[:300]}",
        }
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": f"cloudflare api {resp.status_code}: {resp.text[:200]}",
        }
    rec = (resp.json() or {}).get("result") or {}
    return {
        "ok": True,
        "executed": True,
        "plan": plan,
        "record_id": rec.get("id"),
        "type": rec.get("type"),
        "name": rec.get("name"),
        "content": rec.get("content"),
        "ttl": rec.get("ttl"),
        "proxied": rec.get("proxied"),
        "outcome_score": 1.0,
    }


def _delete_dns_record_handler(
    *,
    zone_name: str | None, zone_id: str | None,
    type: str, name: str,
    record_id: str | None = None,
    dry_run: bool = False,
    token: str | None,
) -> dict[str, Any]:
    """Delete a DNS record. Resolves record_id by (type, name) when
    not supplied. Refuses on multi-match — caller must disambiguate.
    """
    if not token:
        return _not_configured_error()
    if not type or not name:
        return {"ok": False, "error": "type and name are required"}
    type_upper = type.upper()

    with _build_client(token) as client:
        zid, err = _resolve_zone_id(
            zone_name=zone_name, zone_id=zone_id, client=client,
        )
        if err is not None:
            return err

        rec_id = record_id
        existing_content = None
        if rec_id is None:
            existing, lookup_err = _find_existing_record(
                zone_id=zid, type_=type_upper, name=name, client=client,
            )
            if lookup_err is not None:
                return lookup_err
            if existing is None:
                return {
                    "ok": False,
                    "error": (
                        f"no record found in zone {zone_name or zid} "
                        f"matching type={type_upper} name={name!r} — "
                        "nothing to delete."
                    ),
                }
            rec_id = existing.get("id")
            existing_content = existing.get("content")

        plan = {
            "action": "delete",
            "zone_id": zid,
            "zone_name": zone_name,
            "type": type_upper,
            "name": name,
            "record_id": rec_id,
            "existing_content": existing_content,
            "side_effects": [
                f"removes DNS record {type_upper} {name} (irreversible — "
                "Cloudflare doesn't keep deleted record history; you'd "
                "have to re-create it manually if this was wrong)."
            ],
        }
        if dry_run:
            return {"plan": plan, "executed": False, "preview_only": True}

        try:
            resp = client.delete(f"/zones/{zid}/dns_records/{rec_id}")
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"network error during delete: {e}"}

    if resp.status_code in (401, 403):
        return {
            "ok": False,
            "error": (
                f"unauthorized ({resp.status_code}) — token may lack "
                "DNS:Edit scope on this zone."
            ),
        }
    if resp.status_code == 404:
        return {
            "ok": False,
            "error": f"record {rec_id!r} not found (already deleted?).",
        }
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": f"cloudflare api {resp.status_code}: {resp.text[:200]}",
        }
    return {
        "ok": True,
        "executed": True,
        "plan": plan,
        "deleted_record_id": rec_id,
        "outcome_score": 1.0,
    }


def register_cloudflare_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register read-only Cloudflare capabilities."""
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    logger.info(
        "Registering cloudflare.* capabilities: configured=%s, base=%s",
        bool(token), _BASE_URL,
    )

    def list_zones(*, page_size: int = _DEFAULT_PAGE_SIZE) -> dict[str, Any]:
        # Re-read env each call so a token added without restart can work
        # in the next invocation (the boot-time log is informational).
        return _list_zones_handler(
            token=os.environ.get("CLOUDFLARE_API_TOKEN", token),
            page_size=page_size,
        )

    def zone_details(
        *, zone_name: str | None = None,
        zone_id: str | None = None,
    ) -> dict[str, Any]:
        return _zone_details_handler(
            zone_name=zone_name, zone_id=zone_id,
            token=os.environ.get("CLOUDFLARE_API_TOKEN", token),
        )

    def list_dns_records(
        *, zone_name: str | None = None,
        zone_id: str | None = None,
        type: str | None = None,
        name: str | None = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        return _list_dns_records_handler(
            zone_name=zone_name, zone_id=zone_id,
            type=type, name=name,
            token=os.environ.get("CLOUDFLARE_API_TOKEN", token),
            page_size=page_size,
        )

    def set_dns_record(
        *, type: str, name: str, content: str,
        zone_name: str | None = None, zone_id: str | None = None,
        ttl: int = 1,
        proxied: bool | None = None,
        comment: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return _set_dns_record_handler(
            zone_name=zone_name, zone_id=zone_id,
            type=type, name=name, content=content,
            ttl=ttl, proxied=proxied, comment=comment,
            dry_run=dry_run,
            token=os.environ.get("CLOUDFLARE_API_TOKEN", token),
        )

    def delete_dns_record(
        *, type: str, name: str,
        zone_name: str | None = None, zone_id: str | None = None,
        record_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return _delete_dns_record_handler(
            zone_name=zone_name, zone_id=zone_id,
            type=type, name=name, record_id=record_id,
            dry_run=dry_run,
            token=os.environ.get("CLOUDFLARE_API_TOKEN", token),
        )

    registry.register(Capability(
        id="cloudflare.list_zones",
        description=(
            "List every Cloudflare zone on the configured account. "
            "Returns id, name, status, plan, and name servers per zone. "
            "Use this first when the operator asks something general "
            "like 'what domains do I have?' or 'are any of my zones "
            "paused?'. Requires CLOUDFLARE_API_TOKEN with Zone:Read."
        ),
        handler=list_zones,
        input_schema={
            "type": "object",
            "properties": {
                "page_size": {
                    "type": "integer",
                    "description": (
                        "Zones per page (default 100, max 500). The "
                        "operator's account fits on one page in almost "
                        "all cases."
                    ),
                },
            },
            "required": [],
        },
        tier=Tier.READ_EXTERNAL,
        scope="cloudflare",
        audit_required=True,
    ))

    registry.register(Capability(
        id="cloudflare.zone_details",
        description=(
            "Get full details of a single Cloudflare zone — status, "
            "name servers, plan, dev-mode flag, activation date. Pass "
            "either zone_name (e.g. 'windycloud.com') or zone_id; "
            "name lookup adds one extra request. Use cloudflare."
            "list_zones first if you don't know either."
        ),
        handler=zone_details,
        input_schema={
            "type": "object",
            "properties": {
                "zone_name": {
                    "type": "string",
                    "description": (
                        "Domain name (e.g. 'windycloud.com'). Either "
                        "this or zone_id must be provided."
                    ),
                },
                "zone_id": {
                    "type": "string",
                    "description": (
                        "Cloudflare zone id (32-char hash). Either "
                        "this or zone_name must be provided."
                    ),
                },
            },
            "required": [],
        },
        tier=Tier.READ_EXTERNAL,
        scope="cloudflare",
        audit_required=True,
    ))

    registry.register(Capability(
        id="cloudflare.list_dns_records",
        description=(
            "List DNS records for a Cloudflare zone. Returns type, "
            "name, content (the value), TTL, proxied status, and "
            "comment per record. Optional ``type`` filter ('A', "
            "'CNAME', 'MX', 'TXT', etc., case-insensitive) and "
            "``name`` filter (exact FQDN match) narrow the result. "
            "Pass either zone_name or zone_id."
        ),
        handler=list_dns_records,
        input_schema={
            "type": "object",
            "properties": {
                "zone_name": {
                    "type": "string",
                    "description": "Domain name. Either this or zone_id.",
                },
                "zone_id": {
                    "type": "string",
                    "description": "Zone id. Either this or zone_name.",
                },
                "type": {
                    "type": "string",
                    "description": (
                        "Filter by record type (A, AAAA, CNAME, MX, "
                        "TXT, NS, ...). Case-insensitive. Omit to "
                        "return all types."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Filter by exact record name (FQDN). Omit to "
                        "return every name."
                    ),
                },
                "page_size": {
                    "type": "integer",
                    "description": (
                        "Records per page (default 100, max 500)."
                    ),
                },
            },
            "required": [],
        },
        tier=Tier.READ_EXTERNAL,
        scope="cloudflare",
        audit_required=True,
    ))

    registry.register(Capability(
        id="cloudflare.set_dns_record",
        description=(
            "Create or update a DNS record on a Cloudflare zone. "
            "Idempotent (upsert by zone+type+name): if a record with "
            "the same type and name exists, it's PATCHed with the new "
            "content/ttl/proxied/comment; otherwise a new record is "
            "POSTed. STRONG RECOMMENDATION: pass dry_run=true first to "
            "preview the plan — DNS edits affect production traffic "
            "and Cloudflare doesn't keep edit history. Refuses if "
            "more than one record matches type+name (caller must "
            "disambiguate). Tier EXTERNAL_EFFECT — TRUSTED+ band only."
        ),
        handler=set_dns_record,
        input_schema={
            "type": "object",
            "properties": {
                "zone_name": {
                    "type": "string",
                    "description": "Domain name (e.g. 'windycloud.com').",
                },
                "zone_id": {
                    "type": "string",
                    "description": "Zone id; alternative to zone_name.",
                },
                "type": {
                    "type": "string",
                    "description": (
                        "Record type: A, AAAA, CNAME, MX, TXT, NS, "
                        "SRV, CAA, or PTR. Case-insensitive."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Record name (FQDN). For the apex, use the "
                        "zone name itself; otherwise 'sub.zone.com'."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Record value: IP for A/AAAA, target FQDN for "
                        "CNAME, etc."
                    ),
                },
                "ttl": {
                    "type": "integer",
                    "description": (
                        "TTL in seconds. 1 means 'auto' in Cloudflare "
                        "(default). Use 60+ for explicit values."
                    ),
                },
                "proxied": {
                    "type": "boolean",
                    "description": (
                        "Orange-cloud proxy. Only valid for A/AAAA/CNAME. "
                        "Omit to use Cloudflare default on create or "
                        "leave unchanged on update."
                    ),
                },
                "comment": {
                    "type": "string",
                    "description": "Optional human-readable note.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": (
                        "If true, return the plan (including any "
                        "existing record details) without writing. "
                        "STRONGLY recommended for destructive paths."
                    ),
                },
            },
            "required": ["type", "name", "content"],
        },
        tier=Tier.EXTERNAL_EFFECT,
        scope="cloudflare",
        audit_required=True,
    ))

    registry.register(Capability(
        id="cloudflare.delete_dns_record",
        description=(
            "Delete a DNS record. Resolves record_id by zone+type+name "
            "if not provided. Refuses if more than one record matches "
            "type+name. Cloudflare doesn't keep deleted-record history "
            "— this is irreversible without manually re-creating the "
            "record. Pass dry_run=true first. Tier EXTERNAL_EFFECT — "
            "TRUSTED+ band only."
        ),
        handler=delete_dns_record,
        input_schema={
            "type": "object",
            "properties": {
                "zone_name": {"type": "string"},
                "zone_id": {"type": "string"},
                "type": {
                    "type": "string",
                    "description": "Record type (A, CNAME, etc.).",
                },
                "name": {
                    "type": "string",
                    "description": "Record name (FQDN).",
                },
                "record_id": {
                    "type": "string",
                    "description": (
                        "Optional: skip the type+name lookup by "
                        "passing the record id directly. Use when "
                        "multiple records share type+name."
                    ),
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, return the plan without deleting.",
                },
            },
            "required": ["type", "name"],
        },
        tier=Tier.EXTERNAL_EFFECT,
        scope="cloudflare",
        audit_required=True,
    ))
